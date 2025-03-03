"""Child of MicroscopeTranslator, uses TOML configs for params/actions."""

from abc import ABCMeta
import logging
from google.protobuf.message import Message

from ...io.pubsub import publisher as pub
from ...io.control import server as ctrl_srvr

from ...io.protos.generated import scan_pb2
from ...io.protos.generated import control_pb2
from ...io.protos.generated import feedback_pb2
from ...io.protos.generated import signal_pb2

from . import translator
from . import params
from . import actions


logger = logging.getLogger(__name__)


class ConfigTranslator(translator.MicroscopeTranslator, metaclass=ABCMeta):
    """Uses params and actions configs to simplify Microscope communication.

    In the ConfigTranslator, parameter handling and action handling are
    delegated to a ParameterHandler and ActionHandler, respectively. These
    two classes map generic parameter/action names to SPM-specific names,
    and implement the SPM-specific getting/setting calls necessary to
    communicate with the SPM. In doing so, we conceivably simplify the
    definition of a translator to:
    - On the parameters side, the ParameterHandler controls how parameters
    are set/get, which are defined via a simple params_config.toml file.
    - On the actions side,the ActionHandler controls how actions are requested,
    defined via a simple actions_config.toml file.
    - Implementing poll_scope_state(), which likely involves calling
    get_param() with ParameterHandler and converting the data to our
    scan_pb2.ScopeState enum.
    - Implementing poll_scans(), which likely involves using a pre-existing
    Python package to read the specific SPM's save files.
    - Implementing poll_signal(), which likely involves using a pre-existing
    Python package to read the specific SPM's save files. Recall that you may
    simply return an empty Signal1d() if this is not supported.

    Also note that the original abstract action methods defined in
    MicroscopeTranslatorBase do nothing here (since they are already
    handled by the ActionHandler). Overriding them will not do anything.

    Args:
        publisher: Publisher instance, for publishing data.
        control_server: ControlServer instance, for responding to control
            requests.
        param_handler: ParameterHandler class, for handling parameter requests.
        action_handler: ActionHandler class, for handling action requests.
    """

    def __init__(self, name: str, publisher: pub.Publisher,
                 control_server: ctrl_srvr.ControlServer,
                 param_handler: params.ParameterHandler,
                 action_handler: actions.ActionHandler,
                 **kwargs):
        """Init our configured translator.

        Args:
            name: component name.
            publisher: Publisher instance, for publishing data.
            control_server: ControlServer instance, for responding to control
                requests.
            param_handler: ParameterHandler class, for handling parameter
                requests.
            action_handler: ActionHandler class, for handling action requests.
        """
        self.param_handler = param_handler
        self.action_handler = action_handler
        super().__init__(name, publisher, control_server, **kwargs)
        self._validate_required_actions_exist()

    def _validate_required_actions_exist(self):
        """Ensure action_handler at least supports our required actions."""
        for action in actions.REQUIRED_ACTIONS:
            assert action in self.action_handler.actions

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
                      scan_params.data.units,
                      scan_params.data.units,
                      scan_params.spatial.angular_units]

        try:
            self.param_handler.set_param_list(params.SCAN_PARAMS, vals, attr_units)
        except params.ParameterNotSupportedError:
            return control_pb2.ControlResponse.REP_PARAM_NOT_SUPPORTED
        except params.ParameterError:
            return control_pb2.ControlResponse.REP_PARAM_ERROR
        return control_pb2.ControlResponse.REP_SUCCESS

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

        try:
            self.param_handler.set_param_list(attribs, vals, attr_units)
        except params.ParameterNotSupportedError:
            return control_pb2.ControlResponse.REP_PARAM_NOT_SUPPORTED
        except params.ParameterError:
            return control_pb2.ControlResponse.REP_PARAM_ERROR
        return control_pb2.ControlResponse.REP_SUCCESS

    def on_set_probe_pos(self, probe_position: signal_pb2.ProbePosition
                         ) -> control_pb2.ControlResponse:
        """Handle a request to change the probe position of the microscope."""
        vals = [probe_position.point.x,
                probe_position.point.y]
        attr_units = [probe_position.units, probe_position.units]

        try:
            self.param_handler.set_param_list(params.PROBE_POS_PARAMS,
                                            vals, attr_units)
        except params.ParameterNotSupportedError:
            return control_pb2.ControlResponse.REP_PARAM_NOT_SUPPORTED
        except params.ParameterError:
            return control_pb2.ControlResponse.REP_PARAM_ERROR
        return control_pb2.ControlResponse.REP_SUCCESS

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
            logger.warning(f'Feeding parameter {param.parameter}, not in ' +
                           'MicroscopeParameter. Consider adding it in ' +
                           'future.')

        # Try to set (if requested)
        if param.HasField(translator.PARAM_VALUE_ATTRIB):
            try:
                self.param_handler.set_param(param.parameter, param.value,
                                             param.units)
            except params.ParameterNotSupportedError:
                return (control_pb2.ControlResponse.REP_PARAM_NOT_SUPPORTED,
                        None)
            except params.ParameterError:
                return (control_pb2.ControlResponse.REP_PARAM_ERROR,
                        None)

        # Now we should get latest
        try:
            val = self.param_handler.get_param(param.parameter)
            units = self.param_handler.get_unit(param.parameter)
        except params.ParameterNotSupportedError:
            return (control_pb2.ControlResponse.REP_PARAM_NOT_SUPPORTED,
                    None)
        except params.ParameterError:
            return (control_pb2.ControlResponse.REP_PARAM_ERROR,
                    None)

        # Package and return
        rep = control_pb2.ControlResponse.REP_SUCCESS
        param.value = str(val)  # Must convert to str for sending
        if units:  # Set units if this param has any defined.
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
        self._handle_action_not_in_actions(action)

        try:
            self.action_handler.request_action(action.action)
        except actions.ActionNotSupportedError:
            return control_pb2.ControlResponse.REP_ACTION_NOT_SUPPORTED
        except actions.ActionError:
            return control_pb2.ControlResponse.REP_ACTION_ERROR

        return control_pb2.ControlResponse.REP_SUCCESS

    def check_action_support(self, action: control_pb2.ActionMsg
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

        try:
            self.actions._get_action(action)
        except actions.ActionNotSupportedError:
            return control_pb2.ControlResponse.REP_ACTION_NOT_SUPPORTED
        return control_pb2.ControlResponse.REP_SUCCESS

    # ----- Polling Method ----- #
    def poll_scan_params(self) -> scan_pb2.ScanParameters2d:
        """Poll the controller for the current scan parameters.

        Note: data_units are not set here as these are linked to scan
        channel (should be set per-channel in poll_scans()).

        Throw MicroscopeError on failure.
        """
        length_units = self.param_handler.get_unit(
            params.MicroscopeParameter.SCAN_SIZE_X)
        angular_units = self.param_handler.get_unit(
            params.MicroscopeParameter.SCAN_ANGLE)

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
        zctrl_params.integralGain = vals[1]
        zctrl_params.proportionalGain = vals[2]
        zctrl_params.errorGain = vals[3]

        return zctrl_params

    def poll_probe_pos(self) -> signal_pb2.ProbePosition | None:
        """Poll the controller for the current probe position.

        If not supported, return None. Throw MicroscopeError on failure.
        """
        units = self.param_handler.get_unit(
            params.MicroscopeParameter.PROBE_POS_X)
        vals = self.param_handler.get_param_list(params.PROBE_POS_PARAMS)
        probe_pos_params = signal_pb2.ProbePosition()
        probe_pos_params.point.x = vals[0]
        probe_pos_params.point.y = vals[1]
        probe_pos_params.units = units

        return probe_pos_params

    # ----- 'Action' Handlers ----- #
    def on_start_scan(self) -> control_pb2.ControlResponse:
        """Do nothing - handled by ActionHandler."""
        pass

    def on_stop_scan(self) -> control_pb2.ControlResponse:
        """Do nothing - handled by ActionHandler."""
        pass

    def on_start_signal(self) -> control_pb2.ControlResponse:
        """Do nothing - handled by ActionHandler."""
        pass

    def on_stop_signal(self) -> control_pb2.ControlResponse:
        """Do nothing - handled by ActionHandler."""
        pass
