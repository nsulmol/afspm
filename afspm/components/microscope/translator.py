"""Abstract Microscope Translator Class (defines translator logic).

This file contains the base translator class, which shows all the methods
we define as minimum for a translator. Note that there are other child
classes of this one that we suggest for implementing your own translator.

Please see docs/writing_a_microscope_translator.md for guidance.
"""

import os
import logging
import datetime
import copy
from abc import ABCMeta, abstractmethod
from typing import Callable
from types import MappingProxyType
import zmq
from google.protobuf.message import Message

from . import actions

from .. import component as afspmc

from ...io import common
from ...io.pubsub import publisher as pub
from ...io.pubsub import subscriber as sub
from ...io.control import server as ctrl_srvr

from ...io.protos.generated import scan_pb2
from ...io.protos.generated import control_pb2
from ...io.protos.generated import feedback_pb2
from ...io.protos.generated import spec_pb2


logger = logging.getLogger(__name__)


# Protos attributes we check for
TIMESTAMP_ATTRIB = 'timestamp'
ANGLE_ATTRIB = 'angle'
PARAM_VALUE_ATTRIB = 'value'

WARNING_SENT_COUNT = 0  # To ensure we don't spam about Scan2d angle issue


class MicroscopeError(Exception):
    """General MicroscopeTranslator error."""

    pass


class MicroscopeTranslator(afspmc.AfspmComponentBase, metaclass=ABCMeta):
    """Handles communicating with SPM device and handling requests.

    ---
    NOTE: We recommend using ConfigTranslator as your implementation's base
    class, as it may simplify the amount of code writing necessary.
    ---

    The MicroscopeTranslator is the principal node for communicating with an
    SPM device (usually via an actual SPM controller). It is responsible for:
    - Receiving requests from a ControlClient and responding to them;
    - Sending appropriate requests to the device itself, to perform actions;
    - Monitoring the SPM device for state changes, and reporting these changes
    to any listeners via its publisher;
    - Sending out any performed scans out to listeners via its Publisher.

    It communicates with any ControlClients via a zmq REP node, where it
    receives requests and handles them via its appropriate methods (e.g.
    on_start_scan()).

    It sends out state changes and scans via a zmq PUB node, where it publishes
    these aspects.

    This is an abstract class, as device communication is SPM controller
    specific. We expect a MicroscopeTranslator child class for a given SPM
    controller.

    ---
    The MicroscopeTranslator is based around the following capabilities:
    - Setting scan parameters and starting, stopping, and (if supported)
    pausing scans.
    - Modifying the z-controller feedback parameters and slope values
        (to minimize feedback needs).
    - Moving the probe position and collecting 1D specs.

    These capabilities are implemented via set/run calls denominated
    on_XXX() (e.g. on_start_scan), and get calls polled regularly, denominated
    poll_XXX() (e.g. poll_scans). In this base class, these are almost all
    abstract.

    For many experiments, this may be all that is needed! This assumes the
    researcher has already approached their surface and set the necessary
    operating mode / parameters for a scan to run. It also assumes the user
    has set up their scanning 1D spec collection operating modes, so they
    only need to indicate to perform them.

    For additional parameters needing settings, we introduce the REQ_PARAM
    method. This allows getting and setting explicit parameters not defined
    in the on_XXX() / poll_XXX() calls.
    ---

    Notes:
    - we allow providing a subscriber to MicroscopeTranslator (it inherits
    from AspmComponent). If subscribed to the PubSubCache, it will receive
    kill specs and shutdown appropriately.


    Attributes:
        publisher: Publisher instance, for publishing data.
        control_server: ControlServer instance, for responding to control
            requests.
        req_handler_map: mapping from ControlRequest to method to call, for
            ease of use within some of the methods.
        scope_state: device's current ScopeState.
        scan_params; device's current ScanParameters2d.
        scan: device's most recent Scan2d.
        subscriber: optional subscriber, to hook into (and detect) kill
            signals.
    """

    # TODO: Is this the best approach?
    STOP_REQS = [(control_pb2.ControlRequest.REQ_ACTION,
                  control_pb2.ActionMsg(
                      action=actions.MicroscopeAction.STOP_SCAN)),
                 (control_pb2.ControlRequest.REQ_ACTION,
                  control_pb2.ActionMsg(
                      action=actions.MicroscopeAction.STOP_SPEC))]

    # Indicates commands we will allow to be sent while not free
    ALLOWED_COMMANDS_WHILE_NOT_FREE = STOP_REQS

    def __init__(self, name: str, publisher: pub.Publisher,
                 control_server: ctrl_srvr.ControlServer,
                 loop_sleep_s: int = common.LOOP_SLEEP_S,
                 beat_period_s: float = common.HEARTBEAT_PERIOD_S,
                 ctx: zmq.Context = None, subscriber: sub.Subscriber = None):
        """Initialize the translator.

        Args:
            name: component name.
            publisher: Publisher instance, for publishing data.
            control_server: ControlServer instance, for responding to control
                requests.
            loop_sleep_s: how long we sleep in our main loop, in s.
            beat_period_s: how frequently we should send a hearbeat.
            ctx: zmq Context; if not provided, we will create a new instance.
            subscriber: optional subscriber, to hook into (and detect) kill
                signals.
        """
        if not ctx:
            ctx = zmq.Context.instance()

        self.publisher = publisher
        self.control_server = control_server
        self.req_handler_map = self.create_req_handler_map()

        # Init our current understanding of state / params
        self.scope_state = scan_pb2.ScopeState.SS_UNDEFINED
        self._was_interrupted = False
        self.scan_params = scan_pb2.ScanParameters2d()
        self.zctrl_params = feedback_pb2.ZCtrlParameters()
        self.probe_pos = spec_pb2.ProbePosition()

        self.scans = []
        self.signal = None

        # AfspmComponent constructor: no control_client provided, as that
        # logic is handled by the control_server.
        super().__init__(name, subscriber=subscriber, control_client=None,
                         ctx=ctx, loop_sleep_s=loop_sleep_s,
                         beat_period_s=beat_period_s)

    def create_req_handler_map(self) -> dict[control_pb2.ControlRequest,
                                             Callable]:
        """Create our req_handler_map, for mapping REQ to methods."""
        return MappingProxyType({
            control_pb2.ControlRequest.REQ_ACTION: self.on_action_request,
            control_pb2.ControlRequest.REQ_ACTION_SUPPORT:
                self.on_check_action_support,
            control_pb2.ControlRequest.REQ_PARAM: self.on_param_request,
            control_pb2.ControlRequest.REQ_SET_SCAN_PARAMS:
                self.on_set_scan_params,
            control_pb2.ControlRequest.REQ_SET_ZCTRL_PARAMS:
                self.on_set_zctrl_params,
            control_pb2.ControlRequest.REQ_SET_PROBE_POS: self.on_set_probe_pos,
        })

    # ----- 'Action' Handlers ----- #
    @abstractmethod
    def on_start_scan(self) -> control_pb2.ControlResponse:
        """Handle a request to start a scan.

        Must support.
        """

    @abstractmethod
    def on_stop_scan(self) -> control_pb2.ControlResponse:
        """Handle a request to stop a scan.

        Must support.
        """

    @abstractmethod
    def on_start_spec(self) -> control_pb2.ControlResponse:
        """Handle a request to start a spec collection.

        If not supported, return REP_ACTION_NOT_SUPPORTED.
        """

    @abstractmethod
    def on_stop_spec(self) -> control_pb2.ControlResponse:
        """Handle a request to stop a spec collection.

        If not supported, return REP_ACTION_NOT_SUPPORTED.
        """

    # ----- Parameter Handlers ----- #
    @abstractmethod
    def on_set_scan_params(self, scan_params: scan_pb2.ScanParameters2d
                           ) -> control_pb2.ControlResponse:
        """Handle a request to change the scan parameters.

        Must support.
        """

    @abstractmethod
    def on_set_zctrl_params(self, zctrl_params: feedback_pb2.ZCtrlParameters
                            ) -> control_pb2.ControlResponse:
        """Handle a request to change the Z-Controller Feedback parameters.

        If not supported, return REP_CMD_NOT_SUPPORTED.
        """

    @abstractmethod
    def on_set_probe_pos(self, probe_position: spec_pb2.ProbePosition
                         ) -> control_pb2.ControlResponse:
        """Handle a request to change the probe position of the microscope.

        If not supported, return REP_CMD_NOT_SUPPORTED.
        """

    @abstractmethod
    def on_param_request(self, param: control_pb2.ParameterMsg
                         ) -> (control_pb2.ControlResponse,
                               Message | int | None):
        """Set or get a device parameter.

        Respond to a ParameterMsg request.

        Note: if a parameter SET is requested which induces some delay, change
        self.scope_state to SS_BUSY_PARAM within the associated set method, and
        ensure it is updated in poll_scope_state() once ready. This class does
        no special checks for this state, so be careful not to cause your
        translator to get stuck in this state!

        If not supported, return REP_CMD_NOT_SUPPORTED.

        Args:
            param: ParameterMsg request; if value is not provided, treated as
                a 'get' request. Otherwise, treated as a 'set' request.

        Returns:
            - Response to the request.
            - A ParameterMsg response, indicating the state after the set (or
                just the state, if it was a get call).
        """

    def on_action_request(self, action: control_pb2.ActionMsg
                          ) -> control_pb2.ControlResponse:
        """Respond to an action request.

        Given an action, the Microscope should respond.

        Args:
            action: ActionMsg request containing the desired action to be
                performed.

        Returns:
            Response to the request. REP_ACTION_NOT_SUPPORTED if that
            particular action is not supported.
        """
        self._handle_action_not_in_actions(action)

        if action.action == actions.MicroscopeAction.START_SCAN:
            return self.on_start_scan()
        elif action.action == actions.MicroscopeAction.STOP_SCAN:
            return self.on_stop_scan()
        elif action.action == actions.MicroscopeAction.START_SPEC:
            return self.on_start_spec()
        elif action.action == actions.MicroscopeAction.STOP_SPEC:
            return self.on_stop_spec()
        return control_pb2.ControlResponse.REP_ACTION_NOT_SUPPORTED

    def on_check_action_support(self, action: control_pb2.ActionMsg
                                ) -> control_pb2.ControlResponse:
        """Inform whether this translator supports this action.

        Note: it does not *run* the action. Rather, it simply states whether
        or not the given action is supported by this translator. This is used
        to test a translator for support.

        Args:
            action: ActionMsg request containing the desired action to be
                performed.

        Returns:
            Response to the request. REP_ACTION_NOT_SUPPORTED if that
            particular action is not supported.
        """
        self._handle_action_not_in_actions(action)

        SUPPORTED_ACTIONS = [actions.MicroscopeAction.START_SCAN,
                             actions.MicroscopeAction.STOP_SCAN]
        if action.action in SUPPORTED_ACTIONS:
            return control_pb2.ControlResponse.REP_SUCCESS
        return control_pb2.ControlResponse.REP_ACTION_NOT_SUPPORTED

    @staticmethod
    def _handle_action_not_in_actions(action: control_pb2.ActionMsg):
        """Log if a requested action is not in generic actions list."""
        if (action.action not in actions.ACTIONS):
            logger.warning(f'Feeding action {action.action}, not in' +
                           'MicroscopeAction. Consider adding it in ' +
                           'future.')

    # ----- Polling Methods ----- #
    @abstractmethod
    def poll_scope_state(self) -> scan_pb2.ScopeState:
        """Poll the translator for the current scope state.

        Must support. Throw MicroscopeError on failure.
        """

    @abstractmethod
    def poll_scan_params(self) -> scan_pb2.ScanParameters2d:
        """Poll the controller for the current scan parameters.

        Must support. Throw MicroscopeError on failure.
        """

    @abstractmethod
    def poll_zctrl_params(self) -> feedback_pb2.ZCtrlParameters:
        """Poll the controller for the current Z-Control parameters.

        If not supported, return empty ZCtrlParameters.
        Throw MicroscopeError on failure.
        """

    @abstractmethod
    def poll_probe_pos(self) -> spec_pb2.ProbePosition:
        """Poll the controller for the current probe position.

        If not supported, return empty ProbePosition.
        Throw MicroscopeError on failure.
        """

    @abstractmethod
    def poll_scans(self) -> list[scan_pb2.Scan2d]:
        """Obtain latest performed scans.

        We will compare the prior scans (or first of each) to the latest to
        determine if the scan succeeded (i.e. they are different). Note that
        each channel is a different scan! Thus, when we say 'latest scans',
        we really mean the latest single- or multi-channel scan, provided as
        a list of Scan2ds (with each Scan2d being a channel of the scan).

        Note that we will first consider the timestamp attribute when
        comparing scans. If this attribute is not passed, we will do
        a data comparison.

        Must support. Throw MicroscopeError on failure.

        To read the creation time of a file using Python, use
            get_file_modification_datetime()
        and you can put that in the timestamp param with:
            scan.timestamp.FromDatetime(ts)
        """

    @abstractmethod
    def poll_spec(self) -> spec_pb2.Spec1d:
        """Obtain latest performed spec(s).

        We will compare the prior spec(s) to the latest to determine if a
        recent spec collection succeeded (i.e. they are different).

        Note that we will first consider the timestamp attribute when comparing
        specs. If this attribute is not passed, we will do a data comparison.

        If not supported return empty Spec1d.
        Throw MicroscopeError on failure.

        To read the creation time of a file using Python, use
            get_file_modification_datetime()
        and you can put that in the timestamp param with:
            spec.timestamp.FromDatetime(ts)
        """

    def _handle_polling_device(self):
        """Poll aspects of device, and publishes changes (including scans).

        Note: we expect scope state to be sent *last*, so that any client has
        the ability to validate the expected changes have taken effect. Put
        differently: any client should get all other changes *before* the
        state change.
        """
        old_scope_state = copy.deepcopy(self.scope_state)
        self.scope_state = self.poll_scope_state()

        # If we were interrupted, skip the scope state update for one iteration.
        if self._was_interrupted:
            self.scope_state = old_scope_state
            self._was_interrupted = False

        if (old_scope_state == scan_pb2.ScopeState.SS_SCANNING and
                self.scope_state != scan_pb2.ScopeState.SS_SCANNING):
            self._update_scans()

        if (old_scope_state == scan_pb2.ScopeState.SS_SPEC and
                self.scope_state != scan_pb2.ScopeState.SS_SPEC):
            self._update_signals()

        # Handle on non-scope-state parameters
        old_scan_params = copy.deepcopy(self.scan_params)
        self.scan_params = self.poll_scan_params()
        if old_scan_params != self.scan_params:
            logger.info("New scan_params, sending out.")
            self.publisher.send_msg(self.scan_params)

        old_zctrl_params = copy.deepcopy(self.zctrl_params)
        self.zctrl_params = self.poll_zctrl_params()
        if old_zctrl_params != self.zctrl_params:
            logger.info("New zctrl_params, sending out.")
            self.publisher.send_msg(self.zctrl_params)

        old_probe_pos = copy.deepcopy(self.probe_pos)
        self.probe_pos = self.poll_probe_pos()
        if old_probe_pos != self.probe_pos:
            logger.info("New probe position, sending out.")
            self.publisher.send_msg(self.probe_pos)

        # scope state changes sent *last*!
        if old_scope_state != self.scope_state:
            logger.info("New scope state %s, sending out.",
                        common.get_enum_str(scan_pb2.ScopeState,
                                            self.scope_state))
            scope_state_msg = scan_pb2.ScopeStateMsg(
                scope_state=self.scope_state)
            self.publisher.send_msg(scope_state_msg)

    def _update_scans(self):
        old_scans = copy.deepcopy(self.scans)
        self.scans = self.poll_scans()

        # If scans are different, assume new and send out!
        # Test timestamps if they exist. Otherwise, compare
        # data arrays.
        send_scan = False

        both_have_scans = len(self.scans) > 0 and len(old_scans) > 0
        only_new_has_scans = (len(self.scans) > 0 and
                              len(old_scans) == 0)
        both_have_timestamps = both_have_scans and (
            self.scans[0].HasField(TIMESTAMP_ATTRIB) and
            old_scans[0].HasField(TIMESTAMP_ATTRIB))

        # First, check if timestamps are different
        scans_different = both_have_timestamps and (
            self.scans[0].timestamp != old_scans[0].timestamp)
        # Only compare scan data if not the case.
        scans_different = scans_different or both_have_scans and (
            self.scans[0].values != old_scans[0].values)

        if only_new_has_scans or scans_different:
            send_scan = True

        if send_scan:
            logger.info("New scans, sending out.")
            _check_and_warn_angle_issue(self.scans)
            for scan in self.scans:
                self.publisher.send_msg(scan)

    def _update_signals(self):
        old_signal = copy.deepcopy(self.signal)
        self.signal = self.poll_spec()

        # If signal is different, assume new and send out!
        # Test timestamps if they exist. Otherwise, compare
        # data arrays.
        send_signal = False

        both_have_signal = self.signal and old_signal
        only_new_has_signal = self.signal and old_signal is None
        both_have_timestamps = both_have_signal and (
            self.signal.HasField(TIMESTAMP_ATTRIB) and
            old_signal.HasField(TIMESTAMP_ATTRIB))

        # First, check if timestamps are different
        signals_different = both_have_timestamps and (
            self.signal.timestamp != old_signal.timestamp)
        # Only compare signal data if not the case.
        signals_different = signals_different or both_have_signal and (
            self.signal.signals[0].data.dependentValues !=
            old_signal.signals[0].data.dependentValues)  # TODO: Check other values?

        if only_new_has_signal or signals_different:
            send_signal = True

        if send_signal:
            logger.info("New signal, sending out.")
            self.publisher.send_msg(self.signal)

    def _handle_incoming_requests(self):
        """Poll control_server for requests and responds to them."""
        req, proto = self.control_server.poll()
        if req:  # Ensure we received something
            # Refuse most requests while moving/scanning (not free)
            if (self.scope_state != scan_pb2.ScopeState.SS_FREE and
                    (req, proto) not in self.ALLOWED_COMMANDS_WHILE_NOT_FREE):
                self.control_server.reply(
                    control_pb2.ControlResponse.REP_NOT_FREE)
            else:
                handler = self.req_handler_map[req]
                rep = handler(proto) if proto else handler()

                # Special case! If scan or signal was cancelled successfully,
                # we send out an SS_INTERRUPTED state, to allow detecting
                # interruptions.
                if ((req, proto) in self.STOP_REQS and
                        rep == control_pb2.ControlResponse.REP_SUCCESS):
                    scope_state_msg = scan_pb2.ScopeStateMsg(
                        scope_state=scan_pb2.ScopeState.SS_INTERRUPTED)
                    logger.info("Scan interrupted, sending out %s.",
                                common.get_enum_str(
                                    scan_pb2.ScopeState,
                                    scope_state_msg.scope_state))
                    self.publisher.send_msg(scope_state_msg)
                    self.scope_state = scan_pb2.ScopeState.SS_INTERRUPTED
                    self._was_interrupted = True

                # TODO: Special case rep with param
                if isinstance(rep, tuple):  # Special case of rep with obj
                    self.control_server.reply(rep[0], rep[1])
                else:
                    self.control_server.reply(rep)

    def run_per_loop(self):
        """Where we monitor for requests and publish results."""
        self._handle_incoming_requests()
        self._handle_polling_device()


def _check_and_warn_angle_issue(scans: [scan_pb2.Scan2d]):
    """Check if the angle parameter was not set. Warn user if so."""
    global WARNING_SENT_COUNT
    if (len(scans) > 0 and
            not scans[0].params.spatial.roi.HasField(ANGLE_ATTRIB) and
            WARNING_SENT_COUNT == 0):
        logger.warning('Scans received without ROI angle set. If angles '
                       'were used during collection, this can cause issues '
                       'when comparing scan data. Update your translator to '
                       'set this attribute.')
        WARNING_SENT_COUNT += 1


def get_file_modification_datetime(filename: str) -> datetime.datetime:
    """Read modification time of a file, return a datetime representing it.

    Taken from: https://stackoverflow.com/questions/237079/how-do-i-get-file-
    creation-and-modification-date-times.
    """
    return datetime.datetime.fromtimestamp(os.path.getmtime(filename),
                                           tz=datetime.timezone.utc)
