"""Child of MicroscopeTranslator, uses maps for actions/parameters."""

from abc import ABCMeta, abstractmethod
import logging
from google.protobuf.message import Message

from ...io.pubsub import publisher as pub
from ...io.control import server as ctrl_srvr

from ...io.protos.generated import control_pb2

from . import translator
from . import params
from . import actions


logger = logging.getLogger(__name__)


class MapTranslator(translator.MicroscopeTranslator, metaclass=ABCMeta):
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
        self.action_method_map = {}
        super().__init__(name, publisher, control_server, **kwargs)

        self._build_param_method_map()
        self._build_action_method_map()
        self._validate_required_actions_exist()

    @abstractmethod
    def _build_param_method_map(self):
        """Build up param method map."""

    @abstractmethod
    def _build_action_method_map(self):
        """Build up action method map."""

    def _validate_required_actions_exist(self):
        """Ensure action_handler at least supports our required actions."""
        for action in actions.REQUIRED_ACTIONS:
            assert action in self.action_method_map

    def on_param_request(self, param: control_pb2.ParameterMsg
                         ) -> (control_pb2.ControlResponse,
                               Message | int | None):
        """Override method, use param_method_map to map to methods."""
        if param.parameter not in params.PARAMETERS:
            logger.warning(f'Feeding parameter {param.parameter}, not in' +
                           'MicroscopeParameter. Consider adding it in ' +
                           'future.')

        if param.parameter not in self.param_method_map:
            return (control_pb2.ControlResponse.REP_PARAM_NOT_SUPPORTED,
                    param)

        try:
            # Try to set (if requested)
            if param.HasField(translator.PARAM_VALUE_ATTRIB):
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
        self._handle_action_not_in_actions(action)

        # Check methods in action map
        if action.action not in self.action_method_map:
            return control_pb2.ControlResponse.REP_ACTION_NOT_SUPPORTED

        try:
            self.action_method_map[action.action](self)
        except action.ActionError:
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

        # Check methods in action map
        if action.action not in self.action_method_map:
            return control_pb2.ControlResponse.REP_ACTION_NOT_SUPPORTED
        return control_pb2.ControlResponse.REP_SUCCESS

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
