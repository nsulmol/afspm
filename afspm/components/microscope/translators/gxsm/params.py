"""Holds gxsm controller parameters (and other extra logic).

NOTE: gxsm.set() expects a str value, and gxsm.get() returns a float value.
This can be confusing!
"""

import enum
import logging

from afspm.components.microscope import params
from afspm.utils import units

import gxsm


logger = logging.getLogger(__name__)


# GXSM-specific params (only used by internal methods)
MOTOR = 'dsp-fbs-motor'  # coarse motor status.



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
        ret = gxsm.get(attr)
        if ret != self.GET_FAILURE:
            return ret
        else:
            msg = f"Get param failed for {str}"
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
        gxsm.set(attr, str(val))


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




    # In: ParameterHandler, val, curr_units
    # Out: None
    setter: Callable[[Any, str, str], None]

    # In: ParameterHandler
    # Out: val
    getter: Callable[[Any], Any]
