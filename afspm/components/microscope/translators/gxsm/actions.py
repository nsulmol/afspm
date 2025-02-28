"""Holds logic to switch signal called."""

import enum
import logging

from afspm.components.microscope import actions


logger = logging.getLogger(__name__)


GXSM_ACTION_CALL = 'gxsm.action'


class GxsmSignalModeAction(str, enum.Enum):
    """Action names for different signal collection modes."""

    IV_CURVE = 'DSP_VP_IV_EXECUTE'
    Z_SPEC = 'DSP_VP_FZ_EXECUTE'
    BIAS_PULSE = 'DSP_VP_PL_EXECUTE'
    LASER_PULSE = 'DSP_VP_LP_EXECUTE'
    SLOW_PULSE = 'DSP_VP_SP_EXECUTE'
    TIME_SPECTROSCOPY = 'DSP_VP_TS_EXECUTE'
    GENERAL_VECTOR_PROBE = 'DSP_VP_GVP_EXECUTE'

    # This should not be set! Just putting here to not lose it.
    ABORT = 'DSP_VP_ABORT_EXECUTE'


def update_signal_mode(action_handler: actions.ActionHandler,
                       signal_mode_action: GxsmSignalModeAction):
    """Change the signal mode linked to start_signal.

    Args:
        signal_mode_action: enum of desired signal mode.
    """
    logger.debug('Trying to set signal collection mode to '
                 f'{signal_mode_action}.')

    key = actions.ActionParameter.START_SIGNAL
    if key not in action_handler.actions:
        logger.warning(f'{key} was not found in actions config. '
                       'Could not set.')
        return

    # Update the action!
    callable_dict =P{}
    callable_dict[actions.METHOD_KEY] = (GXSM_ACTION_CALL + '(' +
                                         signal_mode_action + ')')
    callable_dict[actions.TYPE_KEY] = actions.CallableType.PASS_ARGS
    callable_dict['uuid'] = signal_mode_action
    action_handler.actions[key] = actions.set_up_callable(callable_dict)
