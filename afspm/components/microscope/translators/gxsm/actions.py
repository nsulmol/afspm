"""Holds logic to switch signal called."""

import enum
import logging

from afspm.components.microscope import actions


logger = logging.getLogger(__name__)


GXSM_ACTION_CALL = 'gxsm.action'
# E.g.: DSP_VP_IV_EXECUTE
GXSM_EXECUTE_ACTION = ('DSP_VP_', '_EXECUTE')
GXSM_ABORT_ACTION = 'DSP_VP_ABORT_EXECUTE'
GXSM_AUTOSAVE_ACTION = 'CHECK-'
GXSM_UNDO_AUTOSAVE_ACTION = 'UNCHECK-'


class GxsmSpecMode(str, enum.Enum):
    """Names for different spec collection modes."""

    IV_CURVE = 'IV'
    Z_SPEC = 'FZ'
    BIAS_PULSE = 'PL'
    LASER_PULSE = 'LP'
    SLOW_PULSE = 'SP'
    TIME_SPECTROSCOPY = 'TS'
    GENERAL_VECTOR_PROBE = 'GVP'


SPEC_MODES = [e.value for e in GxsmSpecMode]


def _get_spec_action_str(spec_mode: GxsmSpecMode) -> str:
    """Create execute action string for spec mode."""
    return GXSM_EXECUTE_ACTION[0] + spec_mode + GXSM_EXECUTE_ACTION[1]


def _get_spec_autosave_str(spec_mode: GxsmSpecMode) -> str:
    """Create autosave action string for spec mode."""
    return GXSM_AUTOSAVE_ACTION + spec_mode


def update_spec_mode(action_handler: actions.ActionHandler,
                     spec_mode: GxsmSpecMode):
    """Change the spec mode linked to start_spec.

    Args:
        spec_mode: enum of desired spec mode.
    """
    logger.debug('Trying to set spec collection mode to '
                 f'{spec_mode}.')

    key = actions.ActionParameter.START_SPEC
    if key not in action_handler.actions:
        logger.warning(f'{key} was not found in actions config. '
                       'Could not set.')
        return

    # Update the action!
    callable_dict = {}

    callable_dict[actions.METHOD_KEY] = GXSM_ACTION_CALL
    callable_dict[actions.TYPE_KEY] = actions.CallableType.PASS_ARGS
    callable_dict['uuid'] = _get_spec_action_str(spec_mode)
    action_handler.actions[key] = actions.set_up_callable(callable_dict)
