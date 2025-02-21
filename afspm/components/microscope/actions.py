"""Holds logic for requesting actions of a microscope.

This package does:
1. Lists the generic action names for various microscope acitons;
2. Holds a mapping from 'generic' action name to the call name (in str form)
needed to perform it.
3. Holds a helper class, ActionHandler, which principally requires any child
class to implement the try_action_spm() method.
"""

import logging

from enum import Enum
from typing import Callable
from abc import ABCMeta, abstractmethod
import tomli

from ...utils.parser import import_from_string


logger = logging.getLogger(__name__)


class MicroscopeAction(str, Enum):
    """Holds generic action names that can be performed."""

    START_SCAN = 'start-scan'
    STOP_SCAN = 'stop-scan'

    START_SIGNAL = 'start-signal'
    STOP_SIGNAL = 'stop-signal'


# Helper so you can do if str in ACTIONS
ACTIONS = [action.value for action in MicroscopeAction]


DESCRIPTIONS = {
    MicroscopeAction.START_SCAN:
    "Start scanning a current ScanParameters location.",
    MicroscopeAction.STOP_SCAN:
    "Stop scanning if in progress.",
    MicroscopeAction.START_SIGNAL:
    "Start performing 1D signal collection.",
    MicroscopeAction.STOP_SIGNAL:
    "Stop performing 1D signal collection if in progress."
}

# TODO: do we need to add start signal/stop signal?
REQUIRED_ACTIONS = [MicroscopeAction.START_SCAN,
                    MicroscopeAction.STOP_SCAN]


class ActionNotSupportedError(Exception):
    """This action is not supported by this microscope."""

    pass


class ActionError(Exception):
    """Generic error occurred requesting an action with this microscope."""

    pass


class ActionHandler(metaclass=ABCMeta):
    """Handles sending action requests to an SPM.

    This abstract class defines the interface for the action handler. For
    actual use, consider one of its children (below).

    This class simplifies sending action requests to a microscope. Rather than
    having a large amount of custom methods, it allows simplified requests by:
    1. Using a config file mechanism to map from generic action names to
    SPM-specific ones.
    2. It requires only implementing the request_action_spm() method, assuming
    all action calls are similar in nature, with only *something* changing
    for different actions (and requiring no input arguments).

    The relationship between generic_params (MicroscopeAction) to this
    *something* is maintained by a TOML config file of str:str key:val pairs,
    which is passed to this class's constructor. The constructor (via
    _build_actions() then builds up a local dict actions, which contains
    the MicroscopeAction:*something* key:val pairs. If this is confusing,
    scroll down to the child classes to see examples of what *something*
    may be.

    Attributes:
        actions: dict of key:val pairs containing generic_param:something,
            where something is an action-specific thing we can use to
            send that action request.
    """

    def __init__(self, actions_config_path: str):
        """Init class, loading actions config for these purposes.

        Args:
            actions_config_path: filepath to TOML dict containing key:val
                pairs associating generic_params to *something*
                action-specific.
        """
        self.actions = {}
        with open(actions_config_path, 'rb') as file:
            actions_config = tomli.load(actions_config_path)
            self._build_actions(actions_config)

    @abstractmethod
    def _build_actions(self, actions_config: dict):
        """Construct self.actions based on actions_config."""

    @abstractmethod
    def request_action(self, generic_action: MicroscopeAction):
        """Request action from SPM given generic action.

        Calls the SPM-specific action for this generic action.

        Args:
            generic_action: Microscope Action wanting to be performed.

        Raises:
            - ActionNotSupportedError if the action was not found in
                the actions_config.
            - ActionError if some other error occurred while asking the
                SPM to perform this action.
        """

    def _get_action(self, generic_action: MicroscopeAction) -> str | Callable:
        """Get Microscope-specific action UUID or action Callable.

        Args:
            generic_action: Microscope Action wanting to be performed.

        Returns:
            scope-specific uuid or action Callable.

        Raises:
            - ActionNotSupportedError if the action was not found in
                the actions_config.
        """
        if generic_action not in self.actions:
            msg = (f'Action {generic_action} not found in params config' +
                   'or value was not string.')
            logger.error(msg)
            raise ActionNotSupportedError(msg)
        return self.actions[generic_action]


class CallableActionHandler(ActionHandler):
    """ActionHandler that maps generic actions to explicit Callables.

    For the config file, we assume a TOML file containing key:val pairs with
    the key corresponding to a generic MicroscopeAction string, and the val
    a string that will be converted to a callable (to be called by
    request_action).

    For example, for a GXSM Controller, where the MicroscopeAction START_SCAN
    is gxsm.start_scan(), the config file would be:
        'start-scan': 'gxsm.start_scan'
    We will import this string to convert it to a Callable and add it to
    self.actions, to be able to be called later.

    The expected Callable format is:
        # In: Nothing
        # Out: Nothing
        Callable[[]]

    Attributes:
        actions: dict of key:val pairs containing generic_param:Callable.
    """

    def _build_actions(self, actions_config: dict):
        for key, val in actions_config.items():
            if isinstance(val, str):
                self.actions[key] = import_from_string(val)

    def request_action(self, generic_action: MicroscopeAction):
        """Request action from SPM given generic action.

        Tries to obtain the SPM-specific callable for this action and then
        runs it.

        Args:
            generic_action: Microscope Action wanting to be performed.

        Raises:
            - ActionNotSupportedError if the action was not found in
                the actions_config.
            - ActionError if some other error occurred while asking the
                SPM to perform this action.
        """
        spm_callable = self._get_action(generic_action)
        spm_callable()


class CallableWithSelfActionHandler(CallableActionHandler):
    """Differs from CallableActionHandler as self is an arg to callable.

    This allows the user to hold state inside self (CallableActionHandler)
    when writing their Callables.

    The expected Callable format is:
        # In: ActionHandler
        # Out: Nothing
        Callable[[ActionHandler]]
    """

    def request_action(self, generic_action: MicroscopeAction):
        """Request action from SPM given generic action.

        Tries to obtain the SPM-specific callable for this action and then
        runs it.

        Args:
            generic_action: Microscope Action wanting to be performed.

        Raises:
            - ActionNotSupportedError if the action was not found in
                the actions_config.
            - ActionError if some other error occurred while asking the
                SPM to perform this action.
        """
        spm_callable = self._get_action(generic_action)
        spm_callable(self)


class StringActionHandler(ActionHandler, metaclass=ABCMeta):
    """ActionHandler that maps generic actions to microscope-specific strs.

    With this ActionHandler, we assume there is a common method
    request_action_spm() which we can call, where the only thing that varies
    between actions is a unique string.

    Attributes:
        actions: dict of key:val pairs containing generic_param:str,
            where str is an action-specific uuid str.
    """

    @abstractmethod
    def request_action_spm(self, spm_uuid: str):
        """Request provided action of SPM.

        This method should only concern itself with sending the specified
        request to the SPM and handling any exceptions with it.

        Raises:
            - ActionError if some other error occurred while asking the
                SPM to perform this action.
        """

    def _build_actions(self, actions_config: dict):
        for key, val in actions_config.items():
            if isinstance(val, str):
                self.actions[key] = val

    def request_action(self, generic_action: MicroscopeAction):
        """Request action from SPM given generic action.

        Tries to obtain the SPM-specific callable for this action and then
        runs it.

        Args:
            generic_action: Microscope Action wanting to be performed.

        Raises:
            - ActionNotSupportedError if the action was not found in
                the actions_config.
            - ActionError if some other error occurred while asking the
                SPM to perform this action.
        """
        spm_uuid = self._get_action(generic_action)
        self.request_action_spm(spm_uuid)
