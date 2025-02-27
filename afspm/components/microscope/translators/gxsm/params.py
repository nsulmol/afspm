"""Holds gxsm controller parameters (and other extra logic).

NOTE: gxsm.set() expects a str value, and gxsm.get() returns a float value.
This can be confusing!
"""

import enum
import logging
from typing import Any

from afspm.components.microscope import params
from afspm.utils import units

import gxsm


logger = logging.getLogger(__name__)


# GXSM-specific params (only used by internal methods)
MOTOR_PARAM = 'dsp-fbs-motor'  # coarse motor status.


class GxsmFeedbackChannel(str, enum.Enum):
    """Holds SPM-specific IDs for the feedback channel names."""

    MIX0 = 'dsp-fbs-mx0-current'
    MIX1 = 'dsp-fbs-mx1-freq'
    MIX2 = 'dsp-fbs-mx2'
    MIX3 = 'dsp-fbs-mx3'


# Combining a Feedback Channel with these strings creates the uuid
# to query them.
GXSM_SETPOINT_END = '-set'
GXSM_EGAIN_END = '-gain'


class GxsmParameterHandler(params.ParameterHandler):
    """Implements GXSM-specific getter/setter for parameter handling."""

    GET_FAILURE = '\x04'

    def get_param_spm(self, spm_uuid: str) -> Any:
        """Get the current value for the microscope parameter.

        This method should only concern itself with requesting an
        scope-specific param and returning the value.

        Args:
            spm_uuid: name of the param in scope-specific terminology.

        Returns:
            Current value.

        Raises:
            ParameterError if getting the parameter fails.
        """
        ret = gxsm.get(spm_uuid)
        if ret != self.GET_FAILURE:
            return ret
        else:
            msg = f"Get param failed for {spm_uuid}"
            logger.error(msg)
            raise params.ParameterError(msg)

    def set_param_spm(self, spm_uuid: str, spm_val: Any):
        """Set the current value for the microscope parameter.

        This method should only concern itself with setting an scope-specific
        param and returning whether it succeeds or not. Conversion to
        scope-expected units should have already been done, and the param
        string should be the one expected by the specific microscope.

        Args:
            spm_uuid: name of the param in scope-specific terminology.
            spm_val: val to set the param to, in scope-specific units.

        Raises:
            - ParameterError if the parameter could not be set.
        """
        gxsm.set(spm_uuid, str(spm_val))

    def update_zctrl_channel(self, channel: GxsmFeedbackChannel):
        """Change the feedback channel in use for ZCtrl.

        Args:
            channel: enum of desired feedback channel.
        """
        logger.debug(f'Trying to set ZCtrl feedback channel to {channel}.')

        keys = [params.MicroscopeParameter.ZCTRL_SETPOINT,
                params.MicroscopeParameter.ZCTRL_EGAIN]
        spm_uuids = [channel + GXSM_SETPOINT_END,
                     channel + GXSM_EGAIN_END]

        for (key, spm_uuid) in zip(keys, spm_uuids):
            if key not in self.param_infos:
                logger.warning(f'{key} was not found in params config. '
                               'Could not set.')
            else:
                self.param_infos[key].uuid = spm_uuid


class GxsmChannelIds(enum.Enum):
    """Channel choice-to-int mapping.

    The int values here correspond to the values gxsm associates to the
    different channel options (in the channel selection menu).

    Remember that (with the exception of TOPO), these do not map to traditional
    channel types (such as phase or magnitude), but to internal system
    specifics (e.g. ADC channel 1).
    """

    OFF = -4
    ACTIVE = enum.auto()
    ON = enum.auto()
    MATH = enum.auto()
    X = enum.auto()
    TOPO = enum.auto()
    MIX1 = enum.auto()
    MIX2 = enum.auto()
    MIX3 = enum.auto()
    ADC0 = enum.auto()
    ADC1 = enum.auto()
    ADC2 = enum.auto()
    ADC3 = enum.auto()
    ADC4 = enum.auto()
    ADC5 = enum.auto()
    ADC6 = enum.auto()
    ADC7 = enum.auto()
    DIDV = enum.auto()
    DDIDV = enum.auto()
    I0_AVG = enum.auto()
    COUNTER = enum.auto()
