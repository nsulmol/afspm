"""Holds asylum controller parameters (and other extra logic)."""

import logging
import enum
from types import MappingProxyType  # Immutable dict

from afspm.components.device import params
from afspm.components.device.controllers.asylum.client import XopClient
from afspm.utils import units


logger = logging.getLogger(__name__)


# All physical  units over the API are stored in meters.
PHYS_UNITS = 'm'

ASYLUM_TRUE = 2  # Hard-coded value for SaveImage/SaveForce


class AsylumMethod(str, enum.Enum):
    """Asylum method names."""

    SCAN_FUNC = 'DoScanFunc'
    START_SCAN_PARAM = 'DoScan_0'  # Param for DoScanFunc
    STOP_SCAN_PARAM = 'StopScan_0'  # Param for DoScanFunc

    GET_VALUE = 'GV'
    SET_VALUE = 'PV'

    GET_STRING = 'GS'
    SET_STRING = 'PS'


class AsylumParam(enum.Enum):
    """Asylum internal parameters.

    Stored as integers, because StrEnums will merge duplicates, and we are
    dealing with a number of duplicate strings here. Thus, we use the below
    param_map to map a parameter enum to its associated string.
    """

    TL_X = enum.auto()  # x-coordinate top-left of scan region (offset).
    TL_Y = enum.auto()  # y-coordinate top-left of scan region (offset).

    SCAN_SIZE = enum.auto()  # major-axis size of scan region.
    SCAN_X_RATIO = enum.auto()  # x-coordinate ratio of scan region.
    SCAN_Y_RATIO = enum.auto()  # y-coordinate ratio of scan region.

    RES_X = enum.auto()  # x-coordinate size of scan array (data points).
    RES_Y = enum.auto()  # y-coordinate size of scan array (data points).

    SCAN_SPEED = enum.auto()  # scanning speed in XX units/s.

    CP = enum.auto()  # proportional gain of main feedback loop.
    CI = enum.auto()  # integral gain of main feedback loop.

    # Don't forget these are in 'igor path' format, use xop methods
    # to convert back.
    IMG_PATH = enum.auto()  # path to saved scans.
    FORCE_PATH = enum.auto()  # path to saved spectroscopic data.

    # Note: diff with IMG_PATH is type: string vs. variable/bool
    SAVE_IMAGE = enum.auto()  # whether or not to save images.
    SAVE_FORCE = enum.auto()  # whether or not to save spectroscopic data.

    SCAN_STATUS = enum.auto()  # scan status.
    FORCE_STATUS = enum.auto()  # spectroscopic status.

    LAST_SCAN = enum.auto()  # are we scanning one time or continuously?


# Creating a dict mapping equivalent to AsylumParameter, to map to necessary
# str values. We need to get *compare* via this mapping, to ensure we
# distinguish between duplicates (this is why we cannot use StrEnum).
PARAM_STR_MAP = MappingProxyType({
    AsylumParam.TL_X: 'XOffset', AsylumParam.TL_Y: 'YOffset',
    AsylumParam.SCAN_SIZE: 'ScanSize', AsylumParam.SCAN_X_RATIO: 'FastRatio',
    AsylumParam.SCAN_Y_RATIO: 'SlowRatio', AsylumParam.RES_X: 'ScanPoints',
    AsylumParam.RES_Y: 'ScanLines', AsylumParam.SCAN_SPEED: 'ScanSpeed',
    AsylumParam.CP: 'ProportionalGain', AsylumParam.CI: 'IntegralGain',
    AsylumParam.IMG_PATH: 'SaveImage', AsylumParam.FORCE_PATH: 'SaveForce',
    AsylumParam.SAVE_IMAGE: 'SaveImage', AsylumParam.SAVE_FORCE: 'SaveForce',
    AsylumParam.SCAN_STATUS: 'ScanStatus', AsylumParam.FORCE_STATUS: 'FMapStatus',
    AsylumParam.LAST_SCAN: 'LastScan'
    })


# Holds which parameters are strings instead of variables
PARAM_IS_STR_TUPLE = (AsylumParam.IMG_PATH, AsylumParam.FORCE_PATH)

# Lookup return indicating a variable lookup failure.
NAN_STR = 'nan'


def _is_variable_lookup_failure(val: float | str | None) -> bool:
    """Check if returned val indicates a variable lookup failure."""
    if isinstance(val, str) and val == NAN_STR:
        return True
    return False


def get_param(client: XopClient, param: AsylumParam) -> float | str:
    """Get asylum parameter.

    Uses the client to get the current value of the provided parameter.

    Args:
        client: XopClient, used to communicate with asylum controller.
        param: AsylumParam to look up.

    Returns:
        Current value (float or str).

    Raises:
        ParameterError if getting the parameter fails.
    """
    # Note: need to do a value comparison
    get_method = (AsylumMethod.GET_STRING if param in PARAM_IS_STR_TUPLE
                  else AsylumMethod.GET_VALUE)

    received, val = client.send_request(get_method,
                                        (PARAM_STR_MAP[param],))
    if received and not _is_variable_lookup_failure(val):
        return val
    msg = f"Get param failed for {param}"
    logger.error(msg)
    raise params.ParameterError(msg)


def get_param_list(client: XopClient, params: tuple[AsylumParam],
                   ) -> tuple[float | str]:
    """Get list of asylum parameters.

    Args:
        client: XopClient, used to communicate with asylum controller.
        params: list of AsylumParams.

    Returns:
        Tuple of received values (float or str for each). Note we return a
        tuple because the type may change of the values. (This is not required,
        but appears to be a good practice in Python, as developers expect
        lists to be of a single type.)

    Raises:
        ParameterError if getting any of the parameters fails. We explicitly
        do this rather than provide a 'None' (or something similar), as we
        do not expect that the user will be able to continue without one of
        the requested parameters.
    """
    return tuple([get_param(client, param) for param in params])


def set_param(client: XopClient, param: AsylumParam, val: str | float,
              curr_unit: str = None, desired_unit: str = PHYS_UNITS) -> bool:
    """Set asylum parameter.

    Given a parameter name and value, attempts to set the asylum controller
    with it. If the value is a float and a curr_unit are provided, we convert
    it if necessary.

    Args:
        client: XopClient, used to communicate with the asylum controller.
        param: AsylumParam to set.
        val: value to set it to.
        curr_unit: units of provided value, as str. Default is None.
        desired_unit: desired units of value, as str. Default is PHYS_UNITS,
            the expected unit of asylum physical units. (We have this to
            allow exceptional overrides).

    Returns:
        True if the set succeeds.
    """
    # Convert value if needed
    if isinstance(val, float):
        try:
            val = units.convert(val, curr_unit, desired_unit)
        except units.ConversionError:
            return False

    set_method = (AsylumMethod.SET_STRING if param in PARAM_IS_STR_TUPLE
                  else AsylumMethod.SET_VALUE)

    received, __ = client.send_request(set_method,
                                       (PARAM_STR_MAP[param], val))
    return received


def set_param_list(client: XopClient, params: tuple[AsylumParam],
                   vals: tuple[str | float], curr_units: tuple[str | None],
                   desired_units: tuple[str | None]) -> bool:
    """Convert a list of values to appropriate units and set them.

    Note: different from set_param in that we validate all conversions can be
    done *before* setting them.

    Args:
        client: XopClient, used to communicate with the asylum controller.
        params: list of params to set.
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

    try:
        converted_vals = units.convert_list(vals, curr_units, desired_units)
    except units.ConversionError:
        return False

    all_received = True
    for val, param in zip(converted_vals, params):
        set_method = (AsylumMethod.SET_STRING if param in PARAM_IS_STR_TUPLE
                      else AsylumMethod.SET_VALUE)
        received, __ = client.send_request(set_method,
                                           (PARAM_STR_MAP[param], val))
        all_received = all_received and received

    if not all_received:
        logger.error("We failed at setting one of the parameters!")
    return all_received
