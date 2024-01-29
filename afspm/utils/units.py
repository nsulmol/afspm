"""Simple method using pint to convert units."""

import logging
from typing import Any

import pint


logger = logging.getLogger(__name__)


# Note: pint's unit registry will exist *HERE*. If you need it, import it in
# your module.
#
ureg = pint.UnitRegistry()
Q_ = ureg.Quantity


def convert(val: Any, unit: str, desired_unit: str) -> Any:
    """Convert a value from one unit to another using pint.

    Args:
        val: input value, of a type that pint supports.
        unit: str representation of your current unit.
        desired_unit: str representation of your desired unit.

    Returns:
        val converted into desired unit.

    Raises:
        DimensionalityError if you try to perform an impossible conversion (or
        one it does not know how to do).
        UndefinedUnitError if either unit or desired_unit are undefined.
    """
    logger.trace("Converting %s from %s to %s", val, unit, desired_unit)
    quantity = val * ureg(unit)
    magnitude = quantity.to(desired_unit).magnitude
    logger.trace("After conversion, magnitude is %s", magnitude)
    return magnitude
