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

from ...io.control.client import ControlClient, send_req_handle_ctrl


logger = logging.getLogger(__name__)


class ScanHandler:
    """Simplifies requesting a scan from a DeviceController.

    The ScanHandler encapsulates the procedure of getting a scan from a
    DeviceController, which involes:
    - Ensuring we have control of the DeviceController.
    - Setting the scan parameters, which may involve moving the device.
    - Obtaining the scan, once the device has been setup (and moved to the
    right location).

    If we do not have control, it will log this and continue requesting control
    between sleeps of rerun_wait_s.

    If the DeviceController returns an unexpected error, it will log this and
    retry the full request (ensuring parameters are set) between sleeps of
    rerun_wait_s.

    If self.get_next_params() returns None (i.e. it is not ready to provide
    the next scan parameters), it will log this and retry requesting between
    sleeps of rerun_wait_s.

    To use within an AfspmComponent:
    - Call on_message_received() within the component's on_message_received().
    - Call handle_issues() within the component's run_per_loop().

    Attributes:
        rerun_wait_s: how long to wait before rerunning our scan (on issues
            with the DeviceController).
        get_next_params: method to determine the next scan_params to scan. If
            None is provided, it will log this and retry later.
        next_params_kwargs: kwargs for get_next_params.
        control_mode_to_run: ControlMode to run under. If the controller is not
            in this control mode, we do nothing until they switch to it.

        _control_mode: current control mode the controller is in.
        _scan_params: current ScanParameters2d instance.
        _scan_state: current ScanState.
        _desired_scan_state: desired ScanState.
        _rerun_scanning_logic: whether or not we need to potentially rerun a
            scan.
        _rerun_sleep_ts: a timestamp for determining when to rerun a scan.
    """

    def __init__(self, rerun_wait_s: int,
                 get_next_params: Callable[[Any],
                                           scan_pb2.ScanParameters2d],
                 next_params_kwargs: dict = None,
                 control_mode: control_pb2.ControlMode =
                 control_pb2.ControlMode.CM_AUTOMATED):
        """Init class."""
        self.rerun_wait_s = rerun_wait_s
        self.get_next_params = get_next_params
        self.next_params_kwargs = (next_params_kwargs if
                                   next_params_kwargs else {})
        self.control_mode_to_run = control_mode

        self._control_mode = control_pb2.ControlMode.CM_UNDEFINED
        self._scan_params = scan_pb2.ScanParameters2d()
        self._scan_state = scan_pb2.ScanState.SS_UNDEFINED
        self._desired_scan_state = scan_pb2.ScanState.SS_UNDEFINED
        self._rerun_sleep_ts = None

    def on_message_received(self, proto: Message,
                            control_client: ControlClient):
        """Handle scanning logic on an AfspmComponent's message receipt.

        This will update the current scan_state and desired_scan_state, and
        send out the next request toward performing a scan.

        It should be called within the associated AfspmComponent's
        on_message_received() method.

        Args:
            proto: Protobuf message received by the AfspmComponent.
            component: AfspmComponent instance.
        """
        if isinstance(proto, control_pb2.ControlState):
            self._control_mode = proto.control_mode
        if isinstance(proto, scan_pb2.ScanStateMsg):
            self._handle_scan_state_receipt(proto)
            self._perform_scanning_logic(control_client)

    def handle_issues(self, control_client: ControlClient):
        """Handle issues requiring us to re-request things.

        Two issues can arrise:
        - DeviceController delays/issues. Here, we need to restart a scan.
        - self.get_next_params() is not ready and has returned None. In this
        case, we need to re-request new params until we receive some.

        This will handle resending requests if we receive delays/issues from
        the DeviceController. It should be called in the associated
        AfspmComponent's run_per_loop() method.

        Note: we assume any appropriate per-loop delaying is handled by the
        AfspmComponent using this handler.

        Args:
            control_client: AfspmComponent's control_client.
        """
        in_desired_control_mode = (self.control_mode_to_run ==
                                   self._control_mode)
        if in_desired_control_mode and self._rerun_sleep_ts is not None:
            enough_time_has_passed = (time.time() - self._rerun_sleep_ts >
                                      self.rerun_wait_s)
            if enough_time_has_passed:
                self._rerun_sleep_ts = None

                # If scan params were not available yet, re-request.
                if not self._scan_params:
                    self._scan_params = self.get_next_params(
                        **self.next_params_kwargs)

                self._perform_scanning_logic(control_client)

    def _handle_scan_state_receipt(self, proto: scan_pb2.ScanStateMsg):
        """Update the desired scan state (getting next scan params if needed).

        If a scan is finished, it also requests the next scan parameters via
        get_next_params().

        Args:
            proto: received ScanStateMsg protobuf from the AfspmComponent.
        """
        logger.debug("Received new scan state: %s",
                     common.get_enum_str(scan_pb2.ScanState,
                                         proto.scan_state))
        last_state = copy.deepcopy(self._scan_state)
        self._scan_state = proto.scan_state

        # Handling desired state logic
        first_startup = (last_state == scan_pb2.ScanState.SS_UNDEFINED and
                         self._scan_state == scan_pb2.ScanState.SS_FREE)
        interrupted = self._scan_state == scan_pb2.SS_INTERRUPTED
        finished_scanning = (last_state == scan_pb2.ScanState.SS_SCANNING and
                             self._scan_state == scan_pb2.ScanState.SS_FREE)
        finished_moving = (last_state == scan_pb2.ScanState.SS_MOVING and
                           self._scan_state == scan_pb2.ScanState.SS_FREE)

        if interrupted:
            logger.info("A scan was interrupted! Will restart what we were "
                        "doing.")
            self._desired_scan_state = scan_pb2.ScanState.SS_MOVING
        elif first_startup or finished_scanning:
            if first_startup:
                logger.info("First startup, sending first scan params.")
            else:
                logger.info("Finished scan, preparing next scan params.")
            self._scan_params = self.get_next_params(**self.next_params_kwargs)
            self._desired_scan_state = scan_pb2.ScanState.SS_MOVING
        elif finished_moving:
            logger.info("Finished moving, will request scan.")
            self._desired_scan_state = scan_pb2.ScanState.SS_SCANNING

    def _perform_scanning_logic(self, control_client: ControlClient):
        """Request the next scan aspect from client.

        Requests the appropriate scan aspect (e.g. set_scan_params, start_scan)
        for the current scan. Handles reruns if a request fails.

        TODO: We are definitely missing the feedback control.

        Args:
            control_client: AfspmComponent's ControlClient.
        """
        in_desired_control_mode = (self.control_mode_to_run ==
                                   self._control_mode)
        scan_state_undefined = (scan_pb2.ScanState.SS_UNDEFINED in
                                (self._scan_state, self._desired_scan_state))
        if scan_state_undefined or not in_desired_control_mode:
            logger.debug("Not performing scanning logic because ScanState "
                         "undefined or ControlMode not desired one.")
            self._handle_rerun(True)
            return  # Early return, we're not ready yet.

        # Handle sending requests (not guaranteed it will work!)
        req_to_call = None
        req_params = {}
        if self._scan_state != self._desired_scan_state:
            logger.info("In state %s, wanting state %s; requesting.",
                        common.get_enum_str(scan_pb2.ScanState,
                                            self._scan_state),
                        common.get_enum_str(scan_pb2.ScanState,
                                            self._desired_scan_state))
            if self._desired_scan_state == scan_pb2.ScanState.SS_MOVING:
                if not self._scan_params:
                    logger.info("Cannot send scan params, because "
                                "get_next_params returned None."
                                "Sleeping and retrying.")
                    self._handle_rerun(True)
                    return
                req_to_call = control_client.set_scan_params
                req_params['scan_params'] = (self._scan_params)
            elif self._desired_scan_state == scan_pb2.ScanState.SS_SCANNING:
                req_to_call = control_client.start_scan

            if not req_to_call:
                return

            rep = send_req_handle_ctrl(control_client, req_to_call,
                                       req_params, self.control_mode_to_run)
            if rep != control_pb2.ControlResponse.REP_SUCCESS:
                logger.info("Sleeping and retrying later.")
                self._handle_rerun(True)

    def _handle_rerun(self, perform_rerun: bool):
        if perform_rerun:
            self._rerun_sleep_ts = time.time()
        else:
            self._rerun_sleep_ts = None


class ScanningComponent(AfspmComponent):
    """Component that sends scan commands to controller.

    This class automatically handles sending scans to the DeviceController,
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
                 control_mode: control_pb2.ControlMode =
                 control_pb2.ControlMode.CM_AUTOMATED, **kwargs):
        """Init class."""
        # Pass self as 'component' to next params method.
        next_params_kwargs['component'] = self
        self.scan_handler = ScanHandler(rerun_wait_s, get_next_params,
                                        next_params_kwargs, control_mode)
        super().__init__(**kwargs)

    def run_per_loop(self):
        """Override to update ScanHandler."""
        self.scan_handler.handle_issues(self.control_client)
        super().run_per_loop()

    def on_message_received(self, envelope: str, proto: Message):
        """Override to run ScanHandler."""
        self.scan_handler.on_message_received(proto, self.control_client)
        super().on_message_received(envelope, proto)
