"""Holds gxsm controller parameters (and other extra logic)."""

import enum
import logging
from typing import Optional, Any

from pint import UndefinedUnitError

from afspm.components.device import params
from afspm.utils import units

from afspm.io.protos.generated import control_pb2

import gxsm


logger = logging.getLogger(__name__)


# ----- Gxsm Params ----- #
class GxsmParameter(str, enum.Enum):
    """Gxsm internal parameters."""
    TL_X = 'OffsetX'
    TL_Y = 'OffsetY'
    SZ_X = 'RangeX'
    SZ_Y = 'RangeY'
    RES_X = 'PointsX'
    RES_Y = 'PointsY'
    SCAN_SPEED_UNITS_S = 'dsp-fbs-scan-speed-scan'
    CP = 'dsp-fbs-cp'
    CI = 'dsp-fbs-ci'
    MOTOR = 'dsp-fbs-motor'


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
    MATH =  enum.auto()
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


def handle_get_set_scan_time(val: Optional[str] = None
                             ) -> (control_pb2.ControlResponse, str):
    """Get/set scantime."""
    if val:
        logger.debug("Scantime to set: %s", val)
        val = scan_time_to_scan_speed(val)
        logger.debug("Setting Scan speed: %s", val)
    res = handle_get_set(GxsmParameter.SCAN_SPEED_UNITS_S, val)
    logger.debug("Gotten scan speed: %s", res[1])
    val = scan_speed_to_scan_time(res[1])
    logger.debug("Gotten scantime: %s", val)
    return res[0], val


def scan_time_to_scan_speed(val: str) -> str:
    """Converts from s / scanline to units / s.

    Receive and return in str; we do the conversion in float within.
    """
    # TODO: Does this support rotations??
    scanline = gxsm.get(GxsmParameter.SZ_X)
    return str(pow(float(val) / scanline, -1))


def scan_speed_to_scan_time(val: str) -> str:
    """Converts from units / s to s / scanline.

    Receive and return in str; we do the conversion in float within.
    Same as prior, copying for ease.
    """
    return scan_time_to_scan_speed(val)


def set_param(attr: str, val: Any, curr_unit: str = None,
              gxsm_unit: str = None) -> bool:
    """Convert a value to gxsm units and set it.

    If curr_unit and gxsm_unit are provided, units.convert is used to try
    and convert to desired units.

    Args:
        attr: name of the attribute, in gxsm terminology.
        val: optional value to set it to.
        curr_unit: unit of provided value. optional.
        gxsm_unit: unit gxsm expects for this value. optional.

    """
    if curr_unit and gxsm_unit and curr_unit != gxsm_unit:
        try:
            val = units.convert(val, curr_unit,
                                gxsm_unit)
        except UndefinedUnitError:
            logger.error("Unable to convert %s from %s to %s.",
                         val, curr_unit, gxsm_unit)
            return False
    gxsm.set(attr, str(val))
    return True


def set_param_list(attrs: list[str], vals: list[Any],
                   curr_units: list[str | None],
                   gxsm_units: list[str | None]) -> bool:
    """Convert a list of values to gxsm units and set them.

    Note: different from set_param in that we validate all conversions
    can be done *before* setting them.
    """
    converted_vals = []
    for val, curr_unit, gxsm_unit in zip(vals, curr_units, gxsm_units):
        if curr_unit and gxsm_unit and curr_unit != gxsm_unit:
            try:
                converted_vals.append(
                    units.convert(val, curr_unit, gxsm_unit))
            except UndefinedUnitError:
                logger.error("Unable to convert %s from %s to %s.",
                             val, curr_unit, gxsm_unit)
                return False
        else:
            converted_vals.append(val)
    for val, attr in zip(converted_vals, attrs):
        gxsm.set(attr, str(val))
    return True


def get_param(attr: str) -> str | None:
    """Get gxsm parameter.

    Gets the current value for the provided parameter. On error, returns
    None.

    Args:
        attr: name of the attribute, in gxsm terminology.

    Returns:
        Current value (in str), or None if could not be obtained.
    """
    ret = gxsm.get(attr)
    return ret if ret != GET_FAILURE else None


def get_param_list(attrs: list[str]) -> list[str] | None:
    """Get list of gxsm attributes.

    Args:
        attrs: list of attribute names, in gxsm terminology.

    Returns:
        List of values, or None if one or more could not be obtained.
    """
    vals = []
    for attr in attrs:
        ret = get_param(attr)
        if ret is None:
            return None
        vals.append(ret)
    return vals


def handle_get_set(attr: str, val: Optional[Any] = None,
                   curr_units: str = None,
                   gxsm_units: str = None):
    """Get (and optionally, set) a gxsm attribute.

    If curr_units and gxsm_units are provided, units.convert is used to try
    and convert to desired units.

    Args:
        attr: name of the attribute, in gxsm terminology.
        val: optional value to set it to.
        curr_units: units of provided value. optional.
        gxsm_units: units gxsm expects for this value. optional.
    """
    if val:
        if not set_param(attr, val, curr_units, gxsm_units):
            return control_pb2.ControlResponse.REP_PARAM_ERROR
    return (control_pb2.ControlResponse.REP_SUCCESS,
            gxsm.get(attr))


PARAM_METHOD_MAP = {
    params.DeviceParameter.SCAN_TIME_S: handle_get_set_scan_time
}
