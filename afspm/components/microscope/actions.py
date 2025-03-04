"""Holds logic for requesting actions of a microscope.

This package does:
1. Lists the generic action names for various microscope acitons;
2. Holds a mapping from 'generic' action name to the call name (in str form)
needed to perform it.
3. Holds a helper class, ActionHandler, which principally requires any child
class to implement the try_action_spm() method.
"""

import logging

import enum
from typing import Callable
from abc import ABCMeta, abstractmethod
from dataclasses import dataclass
import tomli

from ...utils.parser import _evaluate_values_recursively


logger = logging.getLogger(__name__)


class MicroscopeAction(str, enum.Enum):
    """Holds generic action names that can be performed."""

    START_SCAN = 'start-scan'
    STOP_SCAN = 'stop-scan'

    START_SPEC = 'start-spec'
    STOP_SPEC = 'stop-spec'


# Helper so you can do if str in ACTIONS
ACTIONS = [action.value for action in MicroscopeAction]


DESCRIPTIONS = {
    MicroscopeAction.START_SCAN:
    "Start scanning a current ScanParameters location.",
    MicroscopeAction.STOP_SCAN:
    "Stop scanning if in progress.",
    MicroscopeAction.START_SPEC:
    "Start performing 1D spec collection.",
    MicroscopeAction.STOP_SPEC:
    "Stop performing 1D spec collection if in progress."
}

# TODO: do we need to add start spec/stop spec?
REQUIRED_ACTIONS = [MicroscopeAction.START_SCAN,
                    MicroscopeAction.STOP_SCAN]


class ActionNotSupportedError(Exception):
    """This action is not supported by this microscope."""

    pass


class ActionError(Exception):
    """Generic error occurred requesting an action with this microscope."""

    pass


# ----- Action Handling Logic ----- #
# Keys used to populate callables
METHOD_KEY = 'method'
TYPE_KEY = 'type'
PASS_SELF_KEY = 'pass_self'


class CallableType(enum.Enum):
    """Differentiate between callable types, for logic handling below."""

    PASS_KWARGS = enum.auto()  # Dict unpacks when feeding args to method
    PASS_ARGS = enum.auto()  # List unpacks when feeding args to method


@dataclass
class ActionCallable:
    """Holds attributes necessary to run a callable."""

    method: Callable  # Extracted Callable (method).
    kwargs: dict  # Dictionary of kwargs we pass to Callable when calling.
    type: CallableType = CallableType.PASS_ARGS  # Calling logic.
    pass_self: bool = True  # Whether or not to pass ActionHandler in.


def set_up_callable(params_dict: dict) -> ActionCallable:
    """Populate an ActionCallable from dict.

    The dict is expected to have the following keys:
    - METHOD_KEY: str of the Callable, including modules path (see
    parser._evaluate_values_recursively for more info).
    - PASS_SELF_KEY: (optional) bool indicating whether or ont we pass
    ActionHandler as the first argument to the method.
    - TYPE_KEY: (optional) if PASS_KWARGS, we pass the additional key:vals
    as an unpacked dict. If PASS_ARGS, we pass them as an unpacked list.
    Defaults to PASS_ARGS.

    Args:
        params_dict: dict containing what we need to populate our Callable.

    Returns:
        Constructed ActionCallable.

    Raises:
        KeyError if we did not get the required keys.

    """
    if METHOD_KEY not in params_dict:
        msg = (f'Require {METHOD_KEY} to set up a callable. Failed '
               f'for: {params_dict}')
        logger.error(msg)
        raise KeyError(msg)

    evaluated_dict = _evaluate_values_recursively(params_dict)
    kwargs = evaluated_dict  # Do I need to deep copy?

    method = evaluated_dict[METHOD_KEY]
    del kwargs[METHOD_KEY]

    pass_self = False
    if PASS_SELF_KEY in evaluated_dict:
        pass_self = evaluated_dict[PASS_SELF_KEY]
        del kwargs[PASS_SELF_KEY]

    type = CallableType.PASS_ARGS
    if TYPE_KEY in evaluated_dict:
        # Allow CallableType.NORMAL or 'NORMAL' to be provided.
        type = (CallableType[evaluated_dict[TYPE_KEY].upper()] if
                isinstance(evaluated_dict[TYPE_KEY], str) else
                evaluated_dict[TYPE_KEY])
        del kwargs[TYPE_KEY]

    action = ActionCallable(method=method,
                            pass_self=pass_self,
                            type=type,
                            kwargs=kwargs)
    return action


class ActionHandler(metaclass=ABCMeta):
    """Handles sending action requests to an SPM.

    This class simplifies sending action requests to a microscope. Rather than
    having a large amount of custom methods, it allows simplified requests by
    using a config file mechanism to map from generic action names to
    SPM-specific methods. For each supported action, one indicates the
    following in the config file:
    - method: a str of the method to call;
    - pass_self (optional): whether or not the ActionHandler should be passed
        as input. Default to False.
    - type (optional): whether any additional arguments should be fed as
        a list or a dict. If dict, the key:val pairs are fed. If a list,
        only the vals are fed. Linked to CallableType. Defaults to
        PASS_ARGS.
    - additional args (optional): any additional arguments you would feed
    to this method can be passed.
    For example:

        [start-scan]
        method = 'a.b.startscan'
        type = 'PASS_KWARGS'
        pass_self = true
        uuid = 'HELLO'

    NOTE: in TOML, it's true/false, not True/False. Confusing.

    In this case, we will call the method as:
        a.b.startscan(action_handler, uuid='HELLO')

        [start-scan]
        method = 'a.b.startscan'
        type = 'PASS_ARGS'
        uuid = 'HELLO'

    In this case, we will call the method as:
        a.b.startscan('HELLO')

    Attributes:
        actions: dict of key:val pairs containing our method configurations,
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
        with open(actions_config_path, 'rb') as f:
            actions_config = tomli.load(f)
            self._build_actions(actions_config)

    def _build_actions(self, actions_config: dict):
        """Construct self.actions based on actions_config."""
        logger.trace('Building up actions.')
        for key, val in actions_config.items():
            logger.trace(f'Checking {key}:{val.__class__}')
            if isinstance(val, dict):
                logger.trace(f'Trying to set up callable for {key}.')
                action_callable = set_up_callable(val)
                self.actions[key] = action_callable

    def request_action(self, generic_action: MicroscopeAction):
        """Request action from SPM given generic action.

        Calls the SPM-specific ActionCallable for this generic action.

        Args:
            generic_action: Microscope Action wanting to be performed.

        Raises:
            - ActionNotSupportedError if the action was not found in
                the actions_config.
            - ActionError if some other error occurred while asking the
                SPM to perform this action.
        """
        action_callable = self._get_action(generic_action)

        # This is so ugly -- I'm sorry.
        if action_callable.pass_self:
            if action_callable.kwargs:
                if action_callable.type == CallableType.PASS_ARGS:
                    action_callable.method(self,
                                           *action_callable.kwargs.values())
                else:
                    action_callable.method(self, **action_callable.kwargs)
            else:
                action_callable.method(self)
        else:
            if action_callable.kwargs:
                if action_callable.type == CallableType.PASS_ARGS:
                    action_callable.method(*action_callable.kwargs.values())
                else:
                    action_callable.method(**action_callable.kwargs)
            else:
                action_callable.method()

    def _get_action(self, generic_action: MicroscopeAction) -> ActionCallable:
        """Get Microscope-specific ActionCallable.

        Args:
            generic_action: Microscope Action wanting to be performed.

        Returns:
            ActionCallable.

        Raises:
            - ActionNotSupportedError if the action was not found in
                the actions_config.
        """
        if generic_action not in self.actions:
            msg = (f'Action {generic_action} not found in params config ' +
                   'or value was not string.')
            logger.error(msg)
            raise ActionNotSupportedError(msg)
        return self.actions[generic_action]
