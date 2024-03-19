"""Holds gxsm controller parameters (and other extra logic).

NOTE: gxsm.set() expects a str value, and gxsm.get() returns a float value.
This can be confusing!
"""

import enum
import logging
from typing import Optional, Any

from afspm.components.device.controller import DeviceController
from afspm.components.device import params
from afspm.utils import units

from afspm.io.protos.generated import control_pb2

import gxsm


logger = logging.getLogger(__name__)


# ----- Gxsm Params ----- #
class GxsmParameter(str, enum.Enum):
    """Gxsm internal parameters."""

    # Physical scan parameters
    TL_X = 'OffsetX'  # x-coordinate top-left of scan region (offset).
    TL_Y = 'OffsetY'  # y-coordinate top-left of scan region (offset).
    SZ_X = 'RangeX'  # x-coordinate size of scan region.
    SZ_Y = 'RangeY'  # y-coordinate size of scan region.

    # Digital scan parameters
    RES_X = 'PointsX'  # x-coordinate size of scan array (data points).
    RES_Y = 'PointsY'  # y-coordinate size of scan array (data points).

    # Feedback parameters
    CP = 'dsp-fbs-cp'  # proportional gain of main feedback loop.
    CI = 'dsp-fbs-ci'  # integral gain of main feedback loop.

    # Other
    SCAN_SPEED_UNITS_S = 'dsp-fbs-scan-speed-scan'  # scan speed in units/s
    MOTOR = 'dsp-fbs-motor'  # coarse motor status.


GET_FAILURE = '\x04'


class GxsmChannelIds(enum.Enum):
    """Channel choice-to-int mapping.

    The int values here correspond to the values gxsm associates to the
    different channel options (in the channel selection menu).

    Remember that (with the eception of TOPO), these do not map to traditional
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


def set_param(attr: str, val: Any, curr_unit: str = None,
              gxsm_unit: str = None) -> bool:
    """Convert a value to gxsm units and set it.

    If curr_unit and gxsm_unit are provided, units.convert is used to try
    and convert to desired units.

    Args:
        attr: name of the attribute, in gxsm terminology.
        val: value to set it to.
        curr_unit: unit of provided value. optional.
        gxsm_unit: unit gxsm expects for this value. optional.

    """
    try:
        val = units.convert(val, curr_unit, gxsm_unit)
    except units.ConversionError:
        return False

    gxsm.set(attr, str(val))
    return True


def set_param_list(attrs: list[str], vals: list[Any],
                   curr_units: tuple[str | None],
                   gxsm_units: tuple[str | None]) -> bool:
    """Convert a list of values to gxsm units and set them.

    Note: different from set_param in that we validate all conversions
    can be done *before* setting them.
    """
    try:
        converted_vals = units.convert_list(vals, curr_units, gxsm_units)
    except units.ConversionError:
        return False

    for val, attr in zip(converted_vals, attrs):
        gxsm.set(attr, str(val))
    return True


def get_param(attr: str) -> float:
    """Get gxsm parameter.

    Gets the current value for the provided parameter.

    Args:
        attr: name of the attribute, in gxsm terminology.

    Returns:
        Current value (as float).

    Raises:
        ParameterError if getting the parameter fails.
    """
    ret = gxsm.get(attr)
    if ret != GET_FAILURE:
        return ret
    else:
        msg = f"Get param failed for {str}"
        logger.error(msg)
        raise params.ParameterError(msg)


def get_param_list(attrs: list[str]) -> list[float] | None:
    """Get list of gxsm attributes.

    Args:
        attrs: list of attribute names, in gxsm terminology.

    Returns:
        List of values,.

    Raises:
        ParameterError if getting any of the parameters fails. We explicitly
        do this rather than provide a 'None' (or something similar), as we
        do not expect that the user will be able to continue without one of
        the requested parameters.
    """
    return [get_param(attr) for attr in attrs]


def handle_get_set(attr: str, val: Optional[str] = None,
                   curr_units: str = None,
                   gxsm_units: str = None
                   ) -> (control_pb2.ControlResponse, str):
    """Get (and optionally, set) a gxsm attribute.

    If curr_units and gxsm_units are provided, units.convert is used to try
    and convert to desired units.

    Args:
        attr: name of the attribute, in gxsm terminology.
        val: optional value to set it to, as a str.
        curr_units: units of provided value. optional.
        gxsm_units: units gxsm expects for this value. optional.

    Returns:
        Tuple (response, val, units), i.e. containing the control response,
        the value gotten (as a str), and the units of said value (as a str).
    """
    if val:
        if not curr_units or not set_param(attr, val, curr_units, gxsm_units):
            logger.error(f"Unable to set val: {val} with units: {curr_units}")
            return (control_pb2.ControlResponse.REP_PARAM_ERROR, None)
    return (control_pb2.ControlResponse.REP_SUCCESS,
            str(get_param(attr)), gxsm_units)


def get_set_scan_speed(ctrlr: DeviceController, val: Optional[str] = None,
                       units: Optional[str] = None
                       ) -> (control_pb2.ControlResponse, str):
    """Get/set scan speed."""
    gxsm_scan_speed_units = ctrlr.gxsm_physical_units + '/s'
    return handle_get_set(
        GxsmParameter.SCAN_SPEED_UNITS_S, val, curr_units=units,
        gxsm_units=gxsm_scan_speed_units)


PARAM_METHOD_MAP = {
    params.DeviceParameter.SCAN_SPEED: get_set_scan_speed
}
