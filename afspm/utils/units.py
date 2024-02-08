"""Simple method using pint to convert units."""

import logging
from typing import Optional, Any

import pint


logger = logging.getLogger(__name__)


# Note: pint's unit registry will exist *HERE*. If you need it, import it in
# your module.
#
ureg = pint.UnitRegistry()
Q_ = ureg.Quantity


def convert(val: Any, unit: Optional[str] = None,
            desired_unit: Optional[str] = None) -> Any | None:
    """Convert a value from one unit to another using pint.

    If either unit/desired_unit is None or the same, we simply return the
    original value.

    Args:
        val: input value, of a type that pint supports.
        unit: str representation of your current unit.
        desired_unit: str representation of your desired unit.

    Returns:
        val converted into desired unit. None if there is an UndefinedUnitError
            or DimensionalityError.
    """
    if not unit or not desired_unit or unit == desired_unit:
        return val

    try:
        logger.trace("Converting %s from %s to %s", val, unit, desired_unit)
        # Enforce float in conversion always. A str would break this...
        quantity = float(val) * ureg(unit)
        magnitude = quantity.to(desired_unit).magnitude
        logger.trace("After conversion, magnitude is %s", magnitude)
        return magnitude
    except (pint.UndefinedUnitError, pint.DimensionalityError) as err:
        logger.error("Unable to convert %s from %s to %s, due to %s",
                     val, unit, desired_unit,
                     ("undefined unit error."
                      if err is pint.UndefinedUnitError else
                      "dimensionality error."))
        return None


def convert_list(vals: list[Any], units: tuple[str | None],
                 desired_units: tuple[str | None]
                 ) -> list[Any] | None:
    """Convert a list of values to desired units using pint.

    Args:
        vals: list of values, of a type that pint supports.
        units: tuple of strings representing the current units of the provided
            vals. If a particular index is None, we do not convert.
        desired_units: tuple of strings representing the desired units of the
            provided vals. If a particular index is None, we do not convert.

    Returns:
        tuple of values, converted to desired units. If unable to convert one
        (due to an exception), we return None.
    """
    converted_vals = []
    for val, curr_unit, desired_unit in zip(vals, units, desired_units):
        res = convert(val, curr_unit, desired_unit)
        if not res:
            return None
        converted_vals.append(res)
    return tuple(converted_vals)
