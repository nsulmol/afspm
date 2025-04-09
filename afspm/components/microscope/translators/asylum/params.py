"""Holds asylum controller parameters (and other extra logic)."""

from math import isclose
import enum
import logging
from typing import Any

from afspm.utils import units
from afspm.components.microscope import params
from afspm.components.microscope.translators.asylum.client import (
    XopClient, XopMessageError)

from afspm.io.protos.generated import scan_pb2


logger = logging.getLogger(__name__)

# For some things, 'true' is a 2 rather than 1. Strange.
ASYLUM_TRUE = 2


# ----- ScopeState things ----- #
class ScopeState(enum.Flag):
    """Asylum definition of scope states (linked to GET_STATUS_METHOD)."""

    FREE = 0
    SCANNING = 0x1
    ENGAGED = 0x2
    SINGLE_SPEC = 0x10
    SPEC_1 = 0x20  # TODO: Remove me?
    SPEC_2 = 0x40
    SPEC_3 = 0x80
    MOVING = 0x100000


# TODO: Remove me?
ANY_SPEC = (ScopeState.SINGLE_SPEC | ScopeState.SPEC_1 |
            ScopeState.SPEC_2 | ScopeState.SPEC_3)


class AsylumParameterHandler(params.ParameterHandler):
    """Implements asylum-specific getter/setter for parameter handling.

    Attributes:
        client: XopClient, used to communicate with the Asylum controller
            (via the IGOR software).
        spm_uuid_type_map: a map relating spm uuids to their types. Used for
            convenience, to figure out if we need to call GetString or
            GetValue to query the parameter.
    pass
    """

    # Getter/setter method strs
    GET_VALUE = 'GV'
    SET_VALUE = 'PV'

    GET_STRING = 'GS'
    SET_STRING = 'PS'

    def __init__(self, params_config_path: str, client: XopClient):
        """Init our Asylum handler, feeding the Xop Client."""
        if client is None:
            msg = "No xop client provided, cannot continue!"
            logger.critical(msg)
            raise AttributeError(msg)

        self.client = client
        self.spm_uuid_type_map = {}
        super().__init__(params_config_path)
        self._populate_spm_uuid_type_map()

    def _populate_spm_uuid_type_map(self):
        for param_info in self.param_infos.values():
            self.spm_uuid_type_map[param_info.uuid] = param_info.type

    def _obtain_get_set_method(self, spm_uuid: str, request_get: bool) -> str:
        assert spm_uuid in self.spm_uuid_type_map
        if isinstance(self.spm_uuid_type_map[spm_uuid], str):
            return self.GET_STRING if request_get else self.SET_STRING
        else:
            return self.GET_VALUE if request_get else self.SET_VALUE

    def _call_method(self, method_str: str, attrs: tuple = None
                     ) -> (bool, Any | None):
        """Call a method using client and return value (or None).

        This method will catch XOP exceptions and raise a ParameterError if
        the method fails.
        """
        try:
            received, val = self.client.send_request(method_str, attrs)
            if received and not _is_variable_lookup_failure(val):
                return val  # Everything went hunky doery!
        # Exception / issue handling
        except XopMessageError:
            pass

        msg = f"Call method failed for {method_str} with attrs {attrs}."
        logger.error(msg)
        raise params.ParameterError(msg)

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
        method_str = self._obtain_get_set_method(spm_uuid, request_get=True)
        return self._call_method(method_str, (spm_uuid,))

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
        method_str = self._obtain_get_set_method(spm_uuid, request_get=False)
        self._call_method(method_str, (spm_uuid, spm_val))


# Lookup return indicating a variable lookup failure.
NAN_STR = 'nan'


def _is_variable_lookup_failure(val: float | str | None) -> bool:
    """Check if returned val indicates a variable lookup failure."""
    if isinstance(val, str) and val == NAN_STR:
        return True
    return False


class AsylumParam(enum.Enum):
    """Asylum-specific parameters, used as 'generic' names in config.

    We use the 'name' of these parameters as their generic uuid when
    querying them from the params config. So, for example, for SCAN_SIZE,
    we expect:
        [SCAN_SIZE]
        uuid = 'something'
        [...]
    In the config file.

    Note that the type is crucial, as that allows us to know how to call
    the appropriate get/set method (different between str and other types).
    """

    SCAN_SIZE = enum.auto()
    X_RATIO = enum.auto()
    Y_RATIO = enum.auto()
    IMG_PATH = enum.auto()  # Applies both for images and single-force files
    SAVE_IMAGE = enum.auto()  # Applies both for images and single-force files


# Hardcoded Y ratio (for setting)
EXPECTED_Y_RATIO = 1.0


def get_scan_size_x(handler: params.ParameterHandler) -> Any:
    """Get scan size (X-dim) for Asylum.

    The scan size is decoupled in two parameters:
    - ScanSize: 'general' scan size (dimensionless).
    - FastRatio(X)/SlowRatio(Y): The ratio to multiply the scan size in order
    to get that dimension's value.

    This getter will handle that logic.
    """
    generic_uuids = [AsylumParam.SCAN_SIZE.name, AsylumParam.X_RATIO.name]
    vals = handler.get_param_list(generic_uuids)
    return vals[0] * vals[1]  # scan_size * x_ratio


def get_scan_size_y(handler: params.ParameterHandler) -> Any:
    """Get scan size (Y-dim) for Asylum.

    The scan size is decoupled in two parameters:
    - ScanSize: 'general' scan size (dimensionless).
    - FastRatio(X)/SlowRatio(Y): The ratio to multiply the scan size in order
    to get that dimension's value.

    This getter will handle that logic.
    """
    generic_uuids = [AsylumParam.SCAN_SIZE.name, AsylumParam.Y_RATIO.name]
    vals = handler.get_param_list(generic_uuids)
    return vals[0] * vals[1]  # scan_size * y_ratio


def set_scan_size_x(handler: params.ParameterHandler,
                    val: Any, unit: str):
    """Set scan size (X-dim) for Asylum.

    The scan size is decoupled in two parameters:
    - ScanSize: 'general' scan size (dimensionless).
    - FastRatio(X)/SlowRatio(Y): The ratio to multiply the scan size in order
    to get that dimension's value.

    This setter will handle that logic.

    Note: set_scan_size_y needs to be called *before* a call to
    set_scan_size_x, as the latter may change the x_ratio according to the
    *current* scan_size. Since sset_scan_size_y is the one that actually
    sets scan_size, it *must* be called first.
    """
    size_x_uuid = params.MicroscopeParameter.SCAN_SIZE_X
    # Use generic param's info to convert/constrain value.
    param_info = handler._get_param_info(size_x_uuid)
    desired_val = handler._correct_val_for_sending(val, param_info,
                                                   unit)

    # Now, must determine the x ratio for this.
    scan_size = handler.get_param(AsylumParam.SCAN_SIZE.name)
    x_ratio = scan_size / desired_val

    handler.set_param(AsylumParam.X_RATIO.name, x_ratio, curr_unit=None)


def set_scan_size_y(handler: params.ParameterHandler,
                    val: Any, unit: str):
    """Set scan size (Y-dim) for Asylum.

    The scan size is decoupled in two parameters:
    - ScanSize: 'general' scan size (dimensionless).
    - FastRatio(X)/SlowRatio(Y): The ratio to multiply the scan size in order
    to get that dimension's value.

    This setter will handle that logic.

    Note: set_scan_size_y needs to be called *before* a call to
    set_scan_size_x, as the latter may change the x_ratio according to the
    *current* scan_size. Since sset_scan_size_y is the one that actually
    sets scan_size, it *must* be called first.
    """
    size_y_uuid = params.MicroscopeParameter.SCAN_SIZE_Y
    # Use generic param's info to convert/constrain value.
    param_info = handler._get_param_info(size_y_uuid)
    desired_val = handler._correct_val_for_sending(val, param_info,
                                                   unit)

    _ensure_y_ratio_is_1(handler)  # Our logic assumes this!
    handler.set_param(AsylumParam.SCAN_SIZE, desired_val, curr_unit=None)


def _ensure_y_ratio_is_1(handler: params.ParameterHandler):
    """Ensure the scan size y-ratio is 1. If not, fix it and yell."""
    y_ratio = handler.get_param(AsylumParam.Y_RATIO)

    if not isclose(y_ratio, EXPECTED_Y_RATIO):
        logger.warning(f'Scan size FastRatio is not {EXPECTED_Y_RATIO}!'
                       'Going to set, but this is unexpected.')
        handler.set_param(AsylumParam.Y_RATIO, EXPECTED_Y_RATIO,
                          curr_unit=None)


# NOTE: We cannot use GET_VALUE/SET_VALUE with these methods, because they have
# special methods. That is why we are not using handler.set_param_spm here...

# ----- Special method names ----- #
GET_STATUS_METHOD = 'ARGetStatus'
GET_BASENAME_METHOD = 'GetBaseName'
SET_BASENAME_METHOD = 'SetBaseName'
INIT_POS_METHOD = 'InitProbePos'
MOVE_POS_METHOD = 'GoToSpot'
GET_POS_X_METHOD = 'GetProbePosX'
GET_POS_Y_METHOD = 'GetProbePosY'
SET_POS_X_METHOD = 'SetProbePosX'
SET_POS_Y_METHOD = 'SetProbePosY'


def get_probe_pos_x(handler: AsylumParameterHandler) -> Any:
    """Get x-dimension of Probe Position.

    The probe position is stored in the 'scan coordinate system' (CS), meaning
    that the top-left position is treated as origin.

    Thus, we need to add the top-left y-coordinate before returning.
    """
    uuid = params.MicroscopeParameter.SCAN_TOP_LEFT_X
    tl = handler.get_param(uuid)

    pos = handler._call_method(GET_POS_X_METHOD)
    return pos + tl


def get_probe_pos_y(handler: AsylumParameterHandler) -> Any:
    """Get y-dimension of Probe Position.

    The probe position is stored in the 'scan coordinate system' (CS), meaning
    that the top-left position is treated as origin.

    Thus, we need to add the top-left y-coordinate before returning.
    """
    uuid = params.MicroscopeParameter.SCAN_TOP_LEFT_Y
    tl = handler.get_param(uuid)

    pos = handler._call_method(GET_POS_Y_METHOD)
    return pos + tl


def set_probe_pos_x(handler: params.ParameterHandler,
                    val: Any, unit: str):
    """Set x-dimension of probe position.

    The probe position is stored in the 'scan coordinate system' (CS), meaning
    that the top-left position is treated as origin.

    Thus, we need to subtract the top-left x-coordinate before setting.

    NOTE: Assumes SCAN_TOP_LEFT_X and PROBE_POS_X have the same units, and
    using PROBE_POS_X units to convert.
    """
    tl_uuid = params.MicroscopeParameter.SCAN_TOP_LEFT_X
    tl = handler.get_param(tl_uuid)

    uuid = params.MicroscopeParameter.PROBE_POS_X
    param_info = handler._get_param_info(uuid)

    # We need to convert to sending units. This logic *also* happens in
    # _correct_val_for_sending(), but we need to do it here in order
    # to subtract. (the latter method will bound the value using the
    # param_info range)
    val = units.convert(val, unit, param_info.unit)
    val = val - tl

    val = params._correct_val_for_sending(val, param_info, unit, uuid)
    handler._call_method(SET_POS_X_METHOD, (val,))


def set_probe_pos_y(handler: params.ParameterHandler,
                    val: Any, unit: str):
    """Set y-dimension of probe position.

    The probe position is stored in the 'scan coordinate system' (CS), meaning
    that the top-left position is treated as origin.

    Thus, we need to subtract the top-left y-coordinate before setting.

    NOTE: Assumes SCAN_TOP_LEFT_Y and PROBE_POS_Y have the same units, and
    using PROBE_POS_Y units to convert.
    """
    tl_uuid = params.MicroscopeParameter.SCAN_TOP_LEFT_Y
    tl = handler.get_param(tl_uuid)

    uuid = params.MicroscopeParameter.PROBE_POS_Y
    param_info = handler._get_param_info(uuid)

    # We need to convert to sending units. This logic *also* happens in
    # _correct_val_for_sending(), but we need to do it here in order
    # to subtract. (the latter method will bound the value using the
    # param_info range)
    val = units.convert(val, unit, param_info.unit)
    val = val - tl

    val = params._correct_val_for_sending(val, param_info, unit, uuid)
    handler._call_method(SET_POS_Y_METHOD, (val,))
