"""Holds Abstract Microscope Translator Class (defines translator logic).

This file contains three main classes:
- MicroscopeTranslator: the base translator class, which shows all the
methods that must be implemented in order to have a functional translator.
- MapTranslator: a child class, where MicroscopeParameter and MicroscopeAction
controls are delegated to two separate dicts/maps.
- ConfigTranslator: a child class, where MicroscopeParameter and
MicroscopeAction controls are delegated to two separate classes, each using a
configuration file to map generic parameter/actions to methods.

It is recommended to sue the ConfigTranslator as your starting point, as this
has the most inherent functionality (minimizing coding needs for someone
writing a new translator.)
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

from . import params
from . import actions

from .. import component as afspmc

from ...io import common
from ...io.pubsub import publisher as pub
from ...io.pubsub import subscriber as sub
from ...io.control import server as ctrl_srvr

from ...io.protos.generated import scan_pb2
from ...io.protos.generated import control_pb2
from ...io.protos.generated import feedback_pb2


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
    - Moving the probe position and collecting 1D signals. **NOT YET**

    These capabilities are implemented via set/run calls denominated
    on_XXX() (e.g. on_start_scan), and get calls polled regularly, denominated
    poll_XXX() (e.g. poll_scans). In this base class, these are almost all
    abstract.

    For many experiments, this may be all that is needed! This assumes the
    researcher has already approached their surface and set the necessary
    operating mode / parameters for a scan to run. It also assumes the user
    has set up their scanning 1D signal collection operating modes, so they
    only need to indicate to perform them.

    For additional parameters needing settings, we introduce the REQ_PARAM
    method. This allows getting and setting explicit parameters not defined
    in the on_XXX() / poll_XXX() calls.
    ---

    Notes:
    - we allow providing a subscriber to MicroscopeTranslator (it inherits
    from AspmComponent). If subscribed to the PubSubCache, it will receive
    kill signals and shutdown appropriately.


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

    # Indicates commands we will allow to be sent while not free
    ALLOWED_COMMANDS_WHILE_NOT_FREE = [control_pb2.ControlRequest.REQ_STOP_SCAN]

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
        self.scan_params = scan_pb2.ScanParameters2d()
        self.scans = []

        self.zctrl_params = feedback_pb2.ZCtrlParameters()

        # AfspmComponent constructor: no control_client provided, as that
        # logic is handled by the control_server.
        super().__init__(name, subscriber=subscriber, control_client=None,
                         ctx=ctx, loop_sleep_s=loop_sleep_s,
                         beat_period_s=beat_period_s)

    def create_req_handler_map(self) -> dict[control_pb2.ControlRequest,
                                             Callable]:
        """Create our req_handler_map, for mapping REQ to methods."""
        return MappingProxyType({
            control_pb2.ControlRequest.REQ_START_SCAN: self.on_start_scan,
            control_pb2.ControlRequest.REQ_STOP_SCAN:  self.on_stop_scan,
            control_pb2.ControlRequest.REQ_SET_SCAN_PARAMS:
                self.on_set_scan_params,
            control_pb2.ControlRequest.REQ_SET_ZCTRL_PARAMS:
                self.on_set_zctrl_params,
            control_pb2.ControlRequest.REQ_PARAM: self.on_param_request,
            control_pb2.ControlRequest.REQ_ACTION: self.on_action_request,
        })

    # ----- 'Action' Handlers ----- #
    @abstractmethod
    def on_start_scan(self) -> control_pb2.ControlResponse:
        """Handle a request to start a scan."""

    @abstractmethod
    def on_stop_scan(self) -> control_pb2.ControlResponse:
        """Handle a request to stop a scan."""

    # ----- Parameter Handlers ----- #
    @abstractmethod
    def on_set_scan_params(self, scan_params: scan_pb2.ScanParameters2d
                           ) -> control_pb2.ControlResponse:
        """Handle a request to change the scan parameters."""

    @abstractmethod
    def on_set_zctrl_params(self, zctrl_params: feedback_pb2.ZCtrlParameters
                            ) -> control_pb2.ControlResponse:
        """Handle a request to change the Z-Controller Feedback parameters.

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

    @abstractmethod
    def on_action_request(self, action: control_pb2.ActionMsg
                          ) -> control_pb2.ControlResponse:
        """Respond to an action request.

        Given an action, the Microscope should respond.

        Args:
            action: ActionMsg request containing the desired action to be
                performed.

        Returns:
            Response to the request. REP_ACTION
        """

    # ----- Polling Methods ----- #
    @abstractmethod
    def poll_scope_state(self) -> scan_pb2.ScopeState:
        """Poll the translator for the current scope state.

        Throw MicroscopeError on failure.
        """

    @abstractmethod
    def poll_scan_params(self) -> scan_pb2.ScanParameters2d:
        """Poll the controller for the current scan parameters.

        Throw MicroscopeError on failure.
        """

    @abstractmethod
    def poll_zctrl_params(self) -> feedback_pb2.ZCtrlParameters:
        """Poll the controller for the current Z-Control parameters.

        If not supported, return a new ZCtrlParameters instance:
            return feedback_pb2.ZCtrlParameters()

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

        Throw MicroscopeError on failure.

        To read the creation time of a file using Python, use
            get_file_modification_datetime()
        and you can put that in the timestamp param with:
            scan.timestamp.FromDatetime(ts)
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

        if (old_scope_state == scan_pb2.ScopeState.SS_COLLECTING and
                self.scope_state != scan_pb2.ScopeState.SS_COLLECTING):
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

        # scope state changes sent *last*!
        if old_scope_state != self.scope_state:
            logger.info("New scope state %s, sending out.",
                        common.get_enum_str(scan_pb2.ScopeState,
                                            self.scope_state))
            scope_state_msg = scan_pb2.ScopeStateMsg(
                scope_state=self.scope_state)
            self.publisher.send_msg(scope_state_msg)

    def _handle_incoming_requests(self):
        """Poll control_server for requests and responds to them."""
        req, proto = self.control_server.poll()
        if req:  # Ensure we received something
            # Refuse most requests while moving/scanning (not free)
            if (self.scope_state != scan_pb2.ScopeState.SS_FREE and
                    req not in self.ALLOWED_COMMANDS_WHILE_NOT_FREE):
                self.control_server.reply(
                    control_pb2.ControlResponse.REP_NOT_FREE)
            else:
                handler = self.req_handler_map[req]
                rep = handler(proto) if proto else handler()

                # Special case! If scan was cancelled successfully, we
                # send out an SS_INTERRUPTED state, to allow detecting
                # interruptions.
                if (req == control_pb2.ControlRequest.REQ_STOP_SCAN and
                        rep == control_pb2.ControlResponse.REP_SUCCESS):
                    scope_state_msg = scan_pb2.ScopeStateMsg(
                        scope_state=scan_pb2.ScopeState.SS_INTERRUPTED)
                    logger.info("Scan interrupted, sending out %s.",
                                common.get_enum_str(scan_pb2.ScopeState,
                                                    scope_state_msg.scope_state))
                    self.publisher.send_msg(scope_state_msg)

                # TODO: Special case rep with param
                if isinstance(rep, tuple):  # Special case of rep with obj
                    self.control_server.reply(rep[0], rep[1])
                else:
                    self.control_server.reply(rep)

    def run_per_loop(self):
        """Where we monitor for requests and publish results."""
        self._handle_incoming_requests()
        self._handle_polling_device()


class MapTranslator(MicroscopeTranslator, metaclass=ABCMeta):
    """Adds maps to MicroscopeTranslator for parameter and action handling.

    In the MapTranslator, parameter handling and action handling are managed by
    two maps/dicts: self.param_method_map and self.action_method_map. Each of
    these maps a generic ID (params.MicroscopeParameter and
    actions.MicroscopeAction, respectively) to an internal method for handling.

    For parameter handling, each method should have the following prototype:
        # In: MicroscopeTranslator, value (if setting), units (if setting)
        # Out: value, units
        Callable[[MicroscopeTranslator, Any | None, str | None],
                 tuple[str, str]]
    (If there is an error with the get/set, raise a params.ParameterError).

    For action handling, each method should have the following prototype:
        # In: MicroscopeTranslator
        # Out: None
        Callable[[MicroscopeTranslator], None]
    (If there is an error with requesting the action, raise an
    actions.ActionError).

    In both cases, we feed the translator making the call to allow local
    variables/logic to be used.

    Args:
        param_method_map: dict mapping params.MicroscopeParameters to
            individual methods for handling parameter requests.
        action_method_map: dict mapping actions.MicroscopeActions to
            individual methods for handling action requests.
    """

    def __init__(self, name: str, publisher: pub.Publisher,
                 control_server: ctrl_srvr.ControlServer,
                 **kwargs):
        """Init our map translator.

        Args:
            name: component name.
            publisher: Publisher instance, for publishing data.
            control_server: ControlServer instance, for responding to control
                requests.
        """
        self.param_method_map = {}
        self.action_method_map = {}  # TODO: Map start/stop_scan to methods!
        # TODO: Map start/stop signal to methods (when available).
        super().__init__(name, publisher, control_server, **kwargs)

    def on_param_request(self, param: control_pb2.ParameterMsg
                         ) -> (control_pb2.ControlResponse,
                               Message | int | None):
        """Override method, use param_method_map to map to methods."""
        logger.warning(f'checking if we can handle param {param}')
        logger.warning(f'params.PARAMETERS: {params.PARAMETERS}')
        if param.parameter not in params.PARAMETERS:
            return (control_pb2.ControlResponse.REP_PARAM_INVALID,
                    param)

        logger.warning('our param was in our PARAMETERS list. checking if supported.')

        if param.parameter not in self.param_method_map:
            return (control_pb2.ControlResponse.REP_PARAM_NOT_SUPPORTED,
                    param)

        try:
            # Try to set (if requested)
            if param.HasField(PARAM_VALUE_ATTRIB):  # This is a 'set'
                val, units = self.param_method_map[param.parameter](
                    self, param.value, param.units)
            else:  # This is a 'get'
                val, units = self.param_method_map[param.parameter](self)
        except params.ParameterError:
            return (control_pb2.ControlResponse.REP_PARAM_ERROR, param)

        if val:
            param.value = str(val)  # Must convert to str for pb format
            param.units = units
        return (control_pb2.ControlResponse.REP_SUCCESS, param)

    def on_action_request(self, action: control_pb2.ActionMsg
                          ) -> control_pb2.ControlResponse:
        """Respond to an action request.

        Given an action, the Microscope should respond.

        Args:
            action: ActionMsg request containing the desired action to be
                performed.

        Returns:
            Response to the request.
        """
        if (action.action not in actions.ACTIONS):
            return control_pb2.ControlResponse.REP_ACTION_INVALID

        if action.action not in self.action_method_map:
            return control_pb2.ControlResponse.REP_ACTION_NOT_SUPPORTED

        try:
            self.action_method_map[action.action](self)
        except action.ActionError:
            return control_pb2.ControlResponse.REP_ACTION_ERROR

        return control_pb2.ControlResponse.REP_SUCCESS


class ConfigTranslator(MicroscopeTranslator, metaclass=ABCMeta):
    """Uses params and actions configs to simplify Microscope communication.

    In the ConfigTranslator, parameter handling and action handling are
    delegated to a ParameterHandler and ActionHandler, respectively. These
    two classes map generic parameter/action names to SPM-specific names,
    and implement the SPM-specific getting/setting calls necessary to
    communicate with the SPM. In doing so, we conceivably simplify the
    definition of a translator to:
    - On the parameters side, writing the get_param_spm() / set_param_spm()
    methods in ParameterHandler to indicate exactly how to set/get a param;
    and filling out a simple params_config.toml file.
    - On the actions side, writing the request_action_spm() method in
    ActionHandler to indicate exactly how to run an action; and filling out
    a simple actions_config.toml file.
    - Implementing poll_scope_state(), which likely involves calling
    get_param() with ParameterHandler and converting the data to our
    scan_pb2.ScopeState enum.
    - Implementing poll_scans(), which likely involves using a pre-existing
    Python package to read the specific SPM's save files.

    Note that the abstract methods from MicroscopeTranslatorBase that we are
    implementing here assumes no special state / order necessary to set / get
    parameters. If your microscope has some unusual state/order necessary, you
    may need to override some of the methods defined herein.

    Also note that the standard 'action' methods we override here point
    directly to ActionHandler equivalents. Thus, do not override these 'action'
    methods; simply implement the method separately and point to it via the
    ActionHandler config.

    Args:
        publisher: Publisher instance, for publishing data.
        control_server: ControlServer instance, for responding to control
            requests.
        param_handler: ParameterHandler class, for handling parameter requests.
        action_handler: ActionHandler class, for handling action requests.
    """

    def __init__(self, name: str, publisher: pub.Publisher,
                 control_server: ctrl_srvr.ControlServer,
                 params_config_path: str, actions_config_path: str,
                 **kwargs):
        """Init our configured translator.

        Args:
            name: component name.
            publisher: Publisher instance, for publishing data.
            control_server: ControlServer instance, for responding to control
                requests.
            params_config_path: path to our params TOML config file.
            actions_config_path: path to our actions TOML config file.
        """
        self.param_handler = params.ParameterHandler(params_config_path)
        self.action_handler = actions.ActionHandler(actions_config_path)
        super().__init__(name, publisher, control_server, **kwargs)

    # ----- Abstract methods needing implementation ----- #
    @abstractmethod
    def poll_scope_state(self) -> scan_pb2.ScopeState:
        """Poll the translator for the current scope state.

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

        Throw MicroscopeError on failure.

        To read the creation time of a file using Python, use
            get_file_modification_datetime()
        and you can put that in the timestamp param with:
            scan.timestamp.FromDatetime(ts)
        """

    # ----- 'Action' Handlers ----- #
    def on_start_scan(self) -> control_pb2.ControlResponse:
        """Handle a request to start a scan."""
        self.action_handler.request_action(actions.MicroscopeAction.START_SCAN)

    def on_stop_scan(self) -> control_pb2.ControlResponse:
        """Handle a request to stop a scan."""
        self.action_handler.request_action(actions.MicroscopeAction.STOP_SCAN)

    # ----- Parameter Handlers ----- #
    def on_set_scan_params(self, scan_params: scan_pb2.ScanParameters2d
                           ) -> control_pb2.ControlResponse:
        """Handle a request to change the scan parameters."""
        vals = [scan_params.spatial.roi.top_left.x,
                scan_params.spatial.roi.top_left.y,
                scan_params.spatial.roi.size.x,
                scan_params.spatial.roi.size.y,
                scan_params.data.shape.x,
                scan_params.data.shape.y,
                scan_params.spatial.roi.angle]
        attr_units = [scan_params.spatial.length_units,
                      scan_params.spatial.length_units,
                      scan_params.spatial.length_units,
                      scan_params.spatial.length_units,
                      None, None, scan_params.spatial.angular_units]

        self.param_handler.set_param_list(params.SCAN_PARAMS, vals, attr_units)

    def on_set_zctrl_params(self, zctrl_params: feedback_pb2.ZCtrlParameters
                            ) -> control_pb2.ControlResponse:
        """Handle a request to change the Z-Controller Feedback parameters.

        If not supported, return REP_CMD_NOT_SUPPORTED.
        """
        attribs = []
        vals = []
        attr_units = []
        for generic_param, attrib_str in zip(params.ZCTRL_PARAMS,
                                             params.ZCTRL_ATTRIB_STRS):
            if zctrl_params.HasField(attrib_str):
                attribs.append(generic_param)
                vals.append(getattr(zctrl_params, attrib_str))
                attr_units.append(None)

        self.param_handler.set_param_list(attribs, vals, units)

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

        Args:
            param: ParameterMsg request; if value is not provided, treated as
                a 'get' request. Otherwise, treated as a 'set' request.

        Returns:
            - Response to the request.
            - A ParameterMsg response, indicating the state after the set (or
                just the state, if it was a get call).
        """
        if (param.parameter not in params.PARAMETERS):
            return (control_pb2.ControlResponse.REP_PARAM_INVALID,
                    param)

        # Try to set (if requested)
        if param.HasField(PARAM_VALUE_ATTRIB):  # This is a 'set'
            try:
                params.set_param(param.parameter, param.value, param.units)
            except params.ParameterNotSupportedError:
                return (control_pb2.ControlResponse.REP_PARAM_NOT_SUPPORTED,
                        param)
            except params.ParameterError:
                return (control_pb2.ControlResponse.REP_PARAM_ERROR,
                        param)

        # Now we should get latest
        try:
            val = params.get_param(param.parameter)
            units = params.get_units(param.parameter)
        except params.ParameterNotSupportedError:
            return (control_pb2.ControlResponse.REP_PARAM_NOT_SUPPORTED,
                    param)
        except params.ParameterError:
            return (control_pb2.ControlResponse.REP_PARAM_ERROR,
                    param)

        # Package and return
        rep = control_pb2.ControlResponse.REP_SUCCESS
        param.value = str(val)  # Must convert to str for sending
        param.units = units
        return (rep, param)

    def on_action_request(self, action: control_pb2.ActionMsg
                          ) -> control_pb2.ControlResponse:
        """Respond to an action request.

        Given an action, the Microscope should respond.

        Args:
            action: ActionMsg request containing the desired action to be
                performed.

        Returns:
            Response to the request.
        """
        if action.action not in actions.ACTIONS:
            return control_pb2.ControlResponse.REP_ACTION_INVALID

        try:
            actions.request_action(action.action)
        except actions.ActionNotSupportedError:
            return control_pb2.ControlResponse.REP_ACTION_NOT_SUPPORTED
        except actions.ActionError:
            return control_pb2.ControlResponse.REP_ACTION_ERROR

        return control_pb2.ControlResponse.REP_SUCCESS

    # ----- Polling Method ----- #
    def poll_scan_params(self) -> scan_pb2.ScanParameters2d:
        """Poll the controller for the current scan parameters.

        Throw MicroscopeError on failure.
        """
        length_units = self.param_handler.get_units(params.SCAN_SIZE_X)
        angular_units = self.param_handler.get_units(params.SCAN_ANGLE)

        vals = self.param_handler.get_param_list(params.SCAN_PARAMS)

        scan_params = scan_pb2.ScanParameters2d()
        scan_params.spatial.roi.top_left.x = vals[0]
        scan_params.spatial.roi.top_left.y = vals[1]
        scan_params.spatial.roi.size.x = vals[2]
        scan_params.spatial.roi.size.y = vals[3]
        scan_params.spatial.roi.angle = vals[6]
        scan_params.spatial.length_units = length_units
        scan_params.spatial.angular_units = angular_units

        # Note: all gxsm attributes returned as float, must convert to int
        scan_params.data.shape.x = int(vals[4])
        scan_params.data.shape.y = int(vals[5])
        # Not setting data units, as these are linked to scan channel
        return scan_params

    def poll_zctrl_params(self) -> feedback_pb2.ZCtrlParameters:
        """Poll the controller for the current Z-Control parameters.

        If not supported, return a new ZCtrlParameters instance:
            return feedback_pb2.ZCtrlParameters()

        Throw MicroscopeError on failure.
        """
        vals = self.param_handler.get_param_list(params.ZCTRL_PARAMS)

        zctrl_params = feedback_pb2.ZCtrlParameters()
        zctrl_params.setPoint = vals[0]
        zctrl_params.proportionalGain = vals[1]
        zctrl_params.integralGain = vals[2]
        zctrl_params.errorGain = vals[3]

        return zctrl_params


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
