"""Child of MicroscopeTranslator, uses TOML configs for params/actions."""

from abc import ABCMeta
import logging
import copy
from google.protobuf.message import Message

from ...io.pubsub import publisher as pub
from ...io.control import server as ctrl_srvr

from ...io.protos.generated import scan_pb2
from ...io.protos.generated import control_pb2
from ...io.protos.generated import feedback_pb2
from ...io.protos.generated import spec_pb2

from . import translator
from . import params
from . import actions


logger = logging.getLogger(__name__)


DETECTS_MOVING_KEY = 'detects_moving'
PARAM_HANDLER_KEY = 'param_handler'
ACTION_HANDLER_KEY = 'action_handler'


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
    - Implementing poll_spec(), which likely involves using a pre-existing
    Python package to read the specific SPM's save files. Recall that you may
    simply return an empty Spec1d() if this is not supported.

    Also note that the original abstract action methods defined in
    MicroscopeTranslatorBase do nothing here (since they are already
    handled by the ActionHandler). Overriding them will not do anything.

    For scan/spec reading: many microscopes do not actually store the position
    in the scanner range where the collection occurred! For our experiments, we
    want this information. Thus, in this base translator we (a) keep the latest
    scan_params/probe_pos, and (b) have helpers to update the loaded scan/spec
    with this info. These are correct_scan() and correct_spec(), respectively;
    they both also add timestamps. You should use these *even if* your
    microscope does store this information, since the coordinate system we
    use to store this info may be different from your microscopes.

    NOTE: implementing poll_scope_state() is a bit particular; please
    review poll_scope_state's pydoc in translator.py. ConfigTranslator uses a
    variable self.detects_moving to determine if it should call
    self._handle_sending_fake_move() in on_set_probe_pos() /
    on_set_scan_params(). Use this in accordance with that pydoc's guidance.

    Args:
        publisher: Publisher instance, for publishing data.
        control_server: ControlServer instance, for responding to control
            requests.
        param_handler: ParameterHandler class, for handling parameter requests.
        action_handler: ActionHandler class, for handling action requests.
        detects_moving: whether or not the controller can detect SS_MOVING
            events. If False, we will send 'fake' moving events whenever
            the probe position or scan params top-left position change.

        _latest_scan_params: ScanParameters2d from when last scan was
            done. Needed in order to create Scan2d from saved file, as the
            metadata (oddly) does not appear to store the XY origin.
        _latest_probe_pos: ProbePosition of XY position when last spec was
            done. Needed in order to create Spec1d from saved file, as the
            metadata (oddly) does not appear to store the XY position.
    """

    def __init__(self, name: str, publisher: pub.Publisher,
                 control_server: ctrl_srvr.ControlServer,
                 param_handler: params.ParameterHandler,
                 action_handler: actions.ActionHandler,
                 detects_moving: bool = True,
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
            detects_moving: whether or not the controller can detect SS_MOVING
                events.
        """
        self.param_handler = param_handler
        self.action_handler = action_handler
        self.detects_moving = detects_moving

        self._latest_scan_params = None
        self._latest_probe_pos = None

        super().__init__(name, publisher, control_server, **kwargs)
        self._validate_required_actions_exist()

    def _validate_required_actions_exist(self):
        """Ensure action_handler at least supports our required actions."""
        for action in actions.REQUIRED_ACTIONS:
            assert action in self.action_handler.actions

    # ----- Parameter Handlers ----- #
    def on_set_scan_params(self, scan_params: scan_pb2.ScanParameters2d
                           ) -> control_pb2.ControlResponse:
        """Handle a request to change the scan parameters.

        As a reminder: we do not send data units, because the translator
        has no concept of the 'units' of data at the 'scan parameter' level.
        When we read saved scans or specs, we are able to retrieve their
        data units, but this is not something we concern ourselves with at
        the MicroscopeTranslator granularity.
        """
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
                      None, None,
                      scan_params.spatial.angular_units]

        try:
            self.param_handler.set_param_list(params.SCAN_PARAMS, vals,
                                              attr_units)
            if not self.detects_moving:  # Send fake SS_MOVING if needed
                self._handle_sending_fake_move()
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

    def on_set_probe_pos(self, probe_position: spec_pb2.ProbePosition
                         ) -> control_pb2.ControlResponse:
        """Handle a request to change the probe position of the microscope."""
        vals = [probe_position.point.x,
                probe_position.point.y]
        attr_units = [probe_position.units, probe_position.units]

        try:
            self.param_handler.set_param_list(params.PROBE_POS_PARAMS,
                                              vals, attr_units)
            if not self.detects_moving:  # Send fake SS_MOVING if needed
                self._handle_sending_fake_move()
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

        # Store scan_params / probe_pos on action start (for setting
        # later). NOTE: we must do this *here* (rather than on any
        # poll) because the user may change things via the microscope
        # controller UI (separate from our scripts and guardrails).
        if action.action == actions.MicroscopeAction.START_SCAN:
            self._latest_scan_params = self.poll_scan_params()
        elif action.action == actions.MicroscopeAction.START_SPEC:
            self._latest_probe_pos = self.poll_probe_pos()

        try:
            self.action_handler.request_action(action.action)
        except actions.ActionNotSupportedError:
            return control_pb2.ControlResponse.REP_ACTION_NOT_SUPPORTED
        except actions.ActionError:
            return control_pb2.ControlResponse.REP_ACTION_ERROR

        return control_pb2.ControlResponse.REP_SUCCESS

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

        try:
            self.action_handler._get_action(action.action)
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

        return zctrl_params

    def poll_probe_pos(self) -> spec_pb2.ProbePosition | None:
        """Poll the controller for the current probe position.

        If not supported, return None. Throw MicroscopeError on failure.
        """
        units = self.param_handler.get_unit(
            params.MicroscopeParameter.PROBE_POS_X)
        vals = self.param_handler.get_param_list(params.PROBE_POS_PARAMS)
        probe_pos_params = spec_pb2.ProbePosition()
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

    def on_start_spec(self) -> control_pb2.ControlResponse:
        """Do nothing - handled by ActionHandler."""
        pass

    def on_stop_spec(self) -> control_pb2.ControlResponse:
        """Do nothing - handled by ActionHandler."""
        pass


# ----- Scan2d / Spec1d Corrector Helper Methods ----- #
def correct_scan(scan: scan_pb2,
                 scan_params: scan_pb2.ScanParameters2d
                 ) -> list[scan_pb2.Scan2d]:
    """Correct a scan with provided scan params and timestamp info.

    Note that the main attribute we need to correct here is the top-left
    position of the spatial region of interest, as this is what does not
    appear to be stored by any microscope scan format. We assume the other
    parameters (physical size, digital resolution) are stored properly.
    However, to ensure we do not run into units issues, we copy the full
    *spatial* portion of the scan.

    Args:
        scan: scan_pb2.Scan2d to correct.
        scan_params: latest scan parameters, necessary to update the
            origin (as the scan does not seem to record this information).

    Returns:
        corrected scan.
    """
    corrected_scan = copy.deepcopy(scan)

    if not scan_params:
        logger.error('Trying to correct Scan2d without ScanParameters2d!'
                     'We should not have received a scan without scan params!')

    # Scan params update of spatial info
    corrected_scan.params.spatial.CopyFrom(scan_params.spatial)
    # Timestamp update
    ts = translator.get_file_modification_datetime(scan.filename)
    corrected_scan.timestamp.FromDatetime(ts)
    return corrected_scan


def correct_spec(spec: spec_pb2.Spec1d,
                 probe_pos: spec_pb2.ProbePosition | None
                 ) -> spec_pb2.Spec1d:
    """Correct a spec with provided probe position and timestamp info.

    Args:
        spec: spec_pb2.Spec1d to correct.
        probe_pos: latest probe position, used to update.

    Returns:
        Corrected spec.
    """
    corrected_spec = copy.deepcopy(spec)

    if not probe_pos:
        logger.error('Trying to correct Spec1d without ProbePosition!'
                     'We should not have received a spec without a position!')

    # Probe position update
    corrected_spec.position.CopyFrom(probe_pos)
    # Timestamp update
    ts = translator.get_file_modification_datetime(spec.filename)
    corrected_spec.timestamp.FromDatetime(ts)
    return corrected_spec
