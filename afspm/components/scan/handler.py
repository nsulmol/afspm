"""Class to simplify setting scan params and getting a scan."""

import logging
import time
import copy
from typing import Callable, Any

from google.protobuf.message import Message

from ..component import AfspmComponent
from ...io import common

from ...io.protos.generated import scan_pb2
from ...io.protos.generated import control_pb2
from ...io.protos.generated import spec_pb2

from ...io.control.client import ControlClient, send_req_handle_ctrl


logger = logging.getLogger(__name__)


SCAN_PARAMS_KW = 'scan_params'
PROBE_POS_KW = 'probe_pos'


class ScanHandler:
    """Simplifies requesting a scan from a MicroscopeTranslator.

    The ScanHandler encapsulates the procedure of getting a scan or spec
    from a MicroscopeTranslator, which involves:
    - Ensuring we have control of the MicroscopeTranslator.
    - Setting the scan parameters/probe position, which may involve moving the
    device.
    - Obtaining the scan/spec, once the device has been setup (and moved to
    the right location).

    If we do not have control, it will log this and continue requesting control
    between sleeps of rerun_wait_s. Note that this assumes the experiment has
    logged the ExperimentProblem that this ScanHandler supports
    (self.problem_to_solve). If not, we do not request control.

    If the MicroscopeTranslator returns an unexpected error, it will log this
    and retry the full request (ensuring parameters are set) between sleeps of
    rerun_wait_s. If flush_params_on_failure is True, it will reset the params,
    effectively calling get_next_params again. This could be useful if the
    method is time-sensitive, as we cannot predict how long it will take to
    between a failure to request and the next actual request. Since the most
    likely cause is losing control, it may take a while (e.g. an experimental
    problem is being fixed).

    If self.get_next_params() returns None (i.e. it is not ready to provide
    the next scan parameters), it will log this and retry requesting between
    sleeps of rerun_wait_s.

    To use within an AfspmComponent:
    - Call on_message_received() within the component's on_message_received().
    - Call handle_issues() within the component's run_per_loop().

    Attributes:
        rerun_wait_s: how long to wait before rerunning our scan (on issues
            with the MicroscopeTranslator).
        get_next_params: method to determine the next scan_params to scan. If
            None is provided, it will log this and retry later.
        next_params_kwargs: kwargs for get_next_params.
        problem_to_solve: ExperimentProblem the calling component solves (
            EP_NONE if generic). If the scheduler does not have this problem
            flagged, we do nothing until it does.
        component_id: holds str to indicate the component using this handler.
            Used when logging messages.
        flush_params_on_failure: whether or not we reset our params to
            None if we fail to start a scan/spec. Defaults to False.
        _control_state: current ControlState.
        _next_params: current ScanParameters2d or ProbePosition instance.
        _scope_state: current ScopeState.
        _desired_scope_state: desired ScopeState.
        _rerun_scanning_logic: whether or not we need to potentially rerun a
            scan.
        _rerun_sleep_ts: a timestamp for determining when to rerun a scan.
    """

    ParamsCallable = Callable[[Any], scan_pb2.ScanParameters2d |
                              spec_pb2.ProbePosition]

    COLLECTING_STATES = [scan_pb2.ScopeState.SS_SCANNING,
                         scan_pb2.ScopeState.SS_SPEC]
    DEFAULT_FLUSH_PARAMS = False

    def __init__(self, component_id: str, rerun_wait_s: int,
                 get_next_params: Callable[[Any],
                                           scan_pb2.ScanParameters2d],
                 next_params_kwargs: dict = None,
                 problem: control_pb2.ExperimentProblem =
                 control_pb2.ExperimentProblem.EP_NONE,
                 flush_params_on_failure: bool = DEFAULT_FLUSH_PARAMS):
        """Init class."""
        self.rerun_wait_s = rerun_wait_s
        self.get_next_params = get_next_params
        self.next_params_kwargs = (next_params_kwargs if
                                   next_params_kwargs else {})
        self.problem_to_solve = problem
        self.component_id = component_id
        self.flush_params_on_failure = flush_params_on_failure

        self._control_state = None
        self._next_params = None
        self._scope_state = scan_pb2.ScopeState.SS_UNDEFINED
        self._desired_scope_state = scan_pb2.ScopeState.SS_UNDEFINED
        self._rerun_sleep_ts = None

    def on_message_received(self, proto: Message,
                            control_client: ControlClient):
        """Handle scanning logic on an AfspmComponent's message receipt.

        This will update the current scope_state and desired_scope_state, and
        send out the next request toward performing a scan.

        It should be called within the associated AfspmComponent's
        on_message_received() method.

        Args:
            proto: Protobuf message received by the AfspmComponent.
            component: AfspmComponent instance.
        """
        if isinstance(proto, control_pb2.ControlState):
            self._control_state = proto
        if isinstance(proto, scan_pb2.ScopeStateMsg):
            self._handle_scope_state_receipt(proto)
            self._perform_scanning_logic(control_client)

    def handle_issues(self, control_client: ControlClient):
        """Handle issues requiring us to re-request things.

        Two issues can arise:
        - MicroscopeTranslator delays/issues. Here, we need to restart a scan.
        - self.get_next_params() is not ready and has returned None. In this
        case, we need to re-request new params until we receive some.

        This will handle resending requests if we receive delays/issues from
        the MicroscopeTranslator. It should be called in the associated
        AfspmComponent's run_per_loop() method.

        Note: we assume any appropriate per-loop delaying is handled by the
        AfspmComponent using this handler.

        Args:
            control_client: AfspmComponent's control_client.
        """
        problems_set = (self._control_state.problems_set if
                        self._control_state is not None else {})
        problem_in_problems_set = common.is_problem_in_problems_set(
            self.problem_to_solve, problems_set)

        if problem_in_problems_set and self._rerun_sleep_ts is not None:
            enough_time_has_passed = (time.time() - self._rerun_sleep_ts >
                                      self.rerun_wait_s)
            if enough_time_has_passed:
                self._rerun_sleep_ts = None
                self._perform_scanning_logic(control_client)

    def _handle_scope_state_receipt(self, proto: scan_pb2.ScopeStateMsg):
        """Update the desired scope state (getting next scan params if needed).

        If a scan is finished, it also requests the next scan parameters via
        get_next_params().

        Args:
            proto: received ScopeStateMsg protobuf from the AfspmComponent.
        """
        logger.debug(f"{self.component_id}: Received new scope state: %s",
                     common.get_enum_str(scan_pb2.ScopeState,
                                         proto.scope_state))

        if self._control_state is None:
            return  # Early return, as we cannot be sure we are in control.

        last_state = copy.deepcopy(self._scope_state)
        self._scope_state = proto.scope_state

        # Handling desired state logic
        first_startup = (last_state == scan_pb2.ScopeState.SS_UNDEFINED and
                         self._scope_state == scan_pb2.ScopeState.SS_FREE)
        interrupted = self._scope_state == scan_pb2.SS_INTERRUPTED
        finished_collecting = (last_state in self.COLLECTING_STATES and
                               self._scope_state == scan_pb2.ScopeState.SS_FREE)
        finished_moving = (last_state == scan_pb2.ScopeState.SS_MOVING and
                           self._scope_state == scan_pb2.ScopeState.SS_FREE)

        if interrupted:
            logger.info(f"{self.component_id}: A scan was interrupted! "
                        "Will restart what we were doing.")
            self._handle_rerun()
        elif first_startup or finished_collecting:
            if first_startup:
                logger.info(f"{self.component_id}: First startup, sending "
                            "first scan params.")
            else:
                logger.info(f"{self.component_id}: Finished scan, preparing "
                            "next scan params.")
            self._desired_scope_state = scan_pb2.ScopeState.SS_MOVING
        elif finished_moving:
            logger.info(f"{self.component_id}: Finished moving, will request "
                        "collection.")
            self._desired_scope_state = self._get_collection_scope_state(
                self._next_params)

    def _perform_scanning_logic(self, control_client: ControlClient):
        """Request the next scan aspect from client.

        Requests the appropriate scan aspect (e.g. set_scan_params, start_scan)
        for the current scan. Handles reruns if a request fails.

        Args:
            control_client: AfspmComponent's ControlClient.
        """
        problems_set = (self._control_state.problems_set if
                        self._control_state is not None else {})
        problem_in_problems_set = common.is_problem_in_problems_set(
            self.problem_to_solve, problems_set)
        scope_state_undefined = (scan_pb2.ScopeState.SS_UNDEFINED in
                                 (self._scope_state,
                                  self._desired_scope_state))
        if scope_state_undefined or not problem_in_problems_set:
            logger.debug(f"{self.component_id}: Not performing scanning logic "
                         "because ScopeState undefined or problem not in "
                         "problems set.")
            self._handle_rerun()
            return  # Early return, we're not ready yet.

        # Handle sending requests (not guaranteed it will work!)
        req_to_call = None
        req_params = {}
        if self._scope_state != self._desired_scope_state:
            logger.info(f"{self.component_id}: In state %s, wanting "
                        "state %s.",
                        common.get_enum_str(scan_pb2.ScopeState,
                                            self._scope_state),
                        common.get_enum_str(scan_pb2.ScopeState,
                                            self._desired_scope_state))
            if self._desired_scope_state == scan_pb2.ScopeState.SS_MOVING:
                if not self._next_params:  # Get new params if needed
                    self._next_params = self.get_next_params(
                        **self.next_params_kwargs)

                if not self._next_params:  # Handle our getter failing
                    logger.info(f"{self.component_id}: Cannot send params, "
                                   "because get_next_params returned None."
                                   "Sleeping and retrying.")
                    self._handle_rerun()
                    return

                req_to_call, req_params = self._get_set_call_for_next(
                    self._next_params, control_client)

            elif self._desired_scope_state in self.COLLECTING_STATES:
                req_to_call = self._get_collection_call_for_next(
                    self._next_params, control_client)
                # Flush params, so we get the next ones from get_next_params()
                # once our scan is done.
                self._next_params = None

            if not req_to_call:
                return

            rep = send_req_handle_ctrl(control_client, req_to_call,
                                       req_params, self.problem_to_solve)
            if rep != control_pb2.ControlResponse.REP_SUCCESS:
                logger.debug(f"{self.component_id}: Sleeping and retrying "
                             "later.")
                self._handle_rerun()

    @staticmethod
    def _get_set_call_for_next(params: scan_pb2.ScanParameters2d |
                               spec_pb2.ProbePosition,
                               control_client: ControlClient
                               ) -> (Callable, dict):
        """Get set_scan_params or set_probe_pos depending on params fed."""
        req_to_call = None
        req_params = {}
        if isinstance(params, scan_pb2.ScanParameters2d):
            req_to_call = control_client.set_scan_params
            req_params[SCAN_PARAMS_KW] = params
        elif isinstance(params, spec_pb2.ProbePosition):
            req_to_call = control_client.set_probe_pos
            req_params[PROBE_POS_KW] = params
        return req_to_call, req_params

    @staticmethod
    def _get_collection_call_for_next(params: scan_pb2.ScanParameters2d |
                                      spec_pb2.ProbePosition,
                                      control_client: ControlClient
                                      ) -> Callable:
        """Get start_scan or start_spec depending on params fed."""
        req_to_call = None
        if isinstance(params, scan_pb2.ScanParameters2d):
            req_to_call = control_client.start_scan
        elif isinstance(params, spec_pb2.ProbePosition):
            req_to_call = control_client.start_spec
        return req_to_call

    @staticmethod
    def _get_collection_scope_state(params: scan_pb2.ScanParameters2d |
                                    spec_pb2.ProbePosition,
                                    ) -> scan_pb2.ScopeState:
        """Get SS_SCANNING or SS_SPEC depending on params fed."""
        scope_state = scan_pb2.ScopeState.SS_UNDEFINED
        if isinstance(params, scan_pb2.ScanParameters2d):
            scope_state = scan_pb2.ScopeState.SS_SCANNING
        elif isinstance(params, spec_pb2.ProbePosition):
            scope_state = scan_pb2.ScopeState.SS_SPEC
        return scope_state

    def _handle_rerun(self):
        # On a rerun, restart to SS_MOVING (restart the scan)
        self._desired_scope_state = scan_pb2.ScopeState.SS_MOVING

        #  Flush params if we have chosen to do so on failure
        if self.flush_params_on_failure:
            self._next_params = None

        self._rerun_sleep_ts = time.time()


class ScanningComponent(AfspmComponent):
    """Component that sends scan commands to translator.

    This class automatically handles sending scans to the MicroscopeTranslator,
    decided via its get_next_params() method. This is effectively an easier
    way to run a scanning component, if you are only interested in using
    the ScanHandler.

    Note that the get_next_params() method is explicitly fed the component
    as an argument (i.e. component: AfspmComponent is an input argument).

    Attributes:
        scan_handler: ScanHandler instance.
    """

    def __init__(self, rerun_wait_s: int,
                 get_next_params: Callable[[AfspmComponent, Any],
                                           scan_pb2.ScanParameters2d],
                 next_params_kwargs: dict = None,
                 problem: control_pb2.ExperimentProblem =
                 control_pb2.ExperimentProblem.EP_NONE, **kwargs):
        """Init class."""
        # Pass self as 'component' to next params method.
        next_params_kwargs['component'] = self
        super().__init__(**kwargs)
        self.scan_handler = ScanHandler(self.name, rerun_wait_s,
                                        get_next_params,
                                        next_params_kwargs, problem)

    def run_per_loop(self):
        """Override to update ScanHandler."""
        self.scan_handler.handle_issues(self.control_client)
        super().run_per_loop()

    def on_message_received(self, envelope: str, proto: Message):
        """Override to run ScanHandler."""
        self.scan_handler.on_message_received(proto, self.control_client)
        super().on_message_received(envelope, proto)
