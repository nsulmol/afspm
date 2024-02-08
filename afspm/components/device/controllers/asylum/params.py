"""Holds asylum controller parameters (and other extra logic)."""

import logging
import enum
from pint import UndefinedUnitError

from afspm.components.device.controllers.asylum.client import XopClient
from afspm.utils import units


logger = logging.getLogger(__name__)


# All physical  units over the API are stored in meters.
PHYS_UNITS = 'm'


class AsylumBool(int, enum.Enum):
    """Defines true or false in asylum."""
    TRUE = 2
    FALSE = 0


class AsylumMethod(str, enum.Enum):
    """Asylum method names."""
    SCAN = 'DoScanFunc'
    START_SCAN_PARAM = 'DoScan_0'
    STOP_SCAN_PARAM = 'StopScan_0'

    GET_VALUE = 'GV'
    SET_VALUE = 'PV'

    GET_STRING = 'GS'
    SET_STRING = 'PS'


# ----- Asylum Params ----- #
class AsylumParameter(str, enum.Enum):
    """Asylum internal parameter names."""
    TL_X = 'XOffset'
    TL_Y = 'YOffset'

    SCAN_SIZE = 'ScanSize'
    SCAN_X_RATIO = 'Width'
    SCAN_Y_RATIO = 'Height'

    RES_X = 'ScanPoints'
    RES_Y = 'ScanLines'

    SCAN_SPEED_UNITS_S = 'ScanSpeed'

    CP = 'ProportionalGain'
    CI = 'IntegralGain'

    # Don't forget these are in 'igor path' format, use xop methods
    # to convert back.
    IMG_PATH = 'SaveImage'
    FORCE_PATH = 'SaveForce'

    # Note: diff with IMG_PATH is type: string vs. variable/bool
    SAVE_IMAGE = 'SaveImage'
    SAVE_FORCE = 'SaveForce'

    SCAN_STATUS = 'ScanStatus'
    FORCE_STATUS = 'FMapStatus'


# Holds which parameters are strings instead of variables
STR_PARAM_LIST = [AsylumParameter.IMG_PATH, AsylumParameter.FORCE_PATH]


def get_param(attr: str, client: XopClient) -> float | str | None:
    """Get asylum parameter.

    Uses the client to get the current value of the provided parameter. On
    error, returns None.

    Args:
        attr: name of the attribute, in asylum terminology.
        client: XopClient, used to communicate with asylum controller.

    Returns:
        Current value (float or str), or None if could not be obtained.
    """
    get_method = (AsylumMethod.GET_STRING if attr in STR_PARAM_LIST
                  else AsylumMethod.GET_VALUE)

    received, val = client.send_request(get_method, (attr))
    if received:
        return val
    else:
        return None


def get_param_list(attrs: list[str], client: XopClient
                   ) -> tuple[float | str] | None:
    """Get list of asylum parameters.

    Args:
        attrs: list of attribute names, in asylum terminology.
        client: XopClient, used to communicate with asylum controller.

    Returns:
        Tuple of received values (float or str for each) or None if one or more
        could not be obtained. Note we return a tuple because the type may
        change of the values. (This is not required, but appears to be a good
        practice in Python, as developers expect lists to be of a single type.)
    """
    vals = []
    for attr in attrs:
        val = get_param(attr, client)
        if not val:
            return None
        vals.append(val)
    return tuple(vals)


def set_param(attr: str, client: XopClient, val: str | float,
              curr_unit: str = None, desired_unit: str = PHYS_UNITS) -> bool:
    """Set asylum parameter.

    Given an attribute name and value, attempts to set the asylum controller
    with it. If the value is a float and a curr_unit are provided, we convert
    it if necessary.

    Args:
        attr: name of the attribute, in asylum terminology.
        client: XopClient, used to communicate with the asylum controller.
        val: value to set it to.
        curr_unit: units of provided value, as str. Default is None.
        desired_unit: desired units of value, as str. Default is PHYS_UNITS, the
            expected unit of asylum physical units. (We have this to allow
            exceptional overrides).

    Returns:
        True if the set succeeds.
    """

    # Convert value if needed
    if isinstance(val, float):
        val = units.convert(val, curr_unit, desired_unit)
        if not val:
            return False

    set_method = (AsylumMethod.SET_STRING if attr in STR_PARAM_LIST
                  else AsylumMethod.SET_VALUE)

    received, __ = client.send_request(set_method, (attr, val))
    return received


def set_param_list(attrs: list[str], client: XopClient,
                   vals: tuple[str | float], curr_units: tuple[str | None],
                   desired_units: tuple[str | None]) -> bool:
    """Convert a list of values to appropriate units and set them.

    Note: different from set_param in that we validate all conversions can be
    done *before* setting them.

    Args:
        attrs: list of attributes to set.
        client: XopClient, used to communicate with the asylum controller.
        vals: tuple of values to set to.
        curr_units: tuple of units the values are provided in. For a given one,
            if it is None we do not try to convert it.
        desired_units: tuple of units the values should be in. For a given one,
            if it is None we use PHYS_UNITS, the default asylum unit!

    Returns:
        True if all can be set.
    """
    # Replace all desired units that are None with PHYS_UNITS.
    curr_units = [x if x is not None else PHYS_UNITS for x in curr_units]

    converted_vals = units.convert_list(vals, curr_units, desired_units)
    if not converted_vals:
        return False

    all_received = True
    for val, attr in zip(converted_vals, attrs):
        set_method = (AsylumMethod.SET_STRING if attr in STR_PARAM_LIST
                      else AsylumMethod.SET_VALUE)
        received, __ = client.send_request(set_method, (attr, val))
        all_received = all_received and received

    if not all_received:
        logger.error("We failed at setting one of the parameters!")
    return all_received