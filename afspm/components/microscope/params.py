"""Holds logic for setting/getting parameters from a microscope.

This package does 3 things:
1. Lists the generic parameter names for all supported microscope parameters;
2. Holds a mapping from 'generic' MicroscopeParameters to:
    - an spm-specific parameter name;
    - the spm's units for that parameter (if unitless, set to None);
    - an optional min-max range the parameter can be in.
    - an optional getter and setter, which overrides the above. In this case,
        the getter and setter are called direclty, with the generic param (and
        value with unit, if setting).
3. Holds a helper class, ParameterHandler, which principally requires any child
class to implement the get_param_spm()/set_param_spm() methods.
"""

import logging

from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable
from abc import ABCMeta, abstractmethod
import tomli

from ...utils import units
from ...utils.parser import import_from_string


logger = logging.getLogger(__name__)


class MicroscopeParameter(str, Enum):
    """Holds generic parameter names that can be set."""

    SCOPE_STATE = 'scope-state'

    # Physical Scan Parameters
    SCAN_TOP_LEFT_X = 'scan-top-left-x'
    SCAN_TOP_LEFT_Y = 'scan-top-left-y'
    SCAN_SIZE_X = 'scan-size-x'
    SCAN_SIZE_Y = 'scan-size-y'
    SCAN_ANGLE = 'scan-angle'

    # Digital Scan Parameters
    SCAN_RESOLUTION_X = 'scan-resolution-x'
    SCAN_RESOLUTION_Y = 'scan-resolution-y'

    # Feedback Parameters
    ZCTRL_SETPOINT = 'zctrl-setpoint'
    ZCTRL_PGAIN = 'zctrl-pgain'
    ZCTRL_IGAIN = 'zctrl-igain'
    ZCTRL_EGAIN = 'zctrl-egain'

    # Sample Slope Correction
    SAMPLE_SLOPE_X = 'sample-slope-x'
    SAMPLE_SLOPE_Y = 'sample-slope-y'

    # Probe / Scan Parameters
    SCAN_SPEED = 'scan-speed'
    MOVING_SPEED = 'move-speed'

    # Probe Parameters
    PROBE_POS_X = 'probe-pos-x'
    PROBE_POS_Y = 'probe-pos-y'

    # Other
    TIP_BIAS_VOLTAGE = 'tip-bias-voltage'


# Helpers so you can do if str in PARAMETERS
PARAMETERS = [param.value for param in MicroscopeParameter]


DESCRIPTIONS = {
    MicroscopeParameter.SCOPE_STATE:
    "An indication of the state of the microscope (expected to match " +
    "scan_pb2.ScopeState)",

    # Physical Scan Parameters
    MicroscopeParameter.SCAN_TOP_LEFT_X:
    "Top-left position of the 2D scan, x-dimension.",
    MicroscopeParameter.SCAN_TOP_LEFT_Y:
    "Top-left position of the 2D scan, y-dimension.",
    MicroscopeParameter.SCAN_SIZE_X:
    "Size of the 2D scan, x-dimension.",
    MicroscopeParameter.SCAN_SIZE_Y:
    "Size of the 2D scan, y-dimension.",
    MicroscopeParameter.SCAN_ANGLE:
    "Angle of rotation of the 2D scan region.",

    # Digital Scan Parameters
    MicroscopeParameter.SCAN_RESOLUTION_X:
    "Number of points recorded for the x-dimension of the scan region.",
    MicroscopeParameter.SCAN_RESOLUTION_Y:
    "Number of points recorded for the y-dimension of the scan region.",

    # Feedback Parameters
    MicroscopeParameter.ZCTRL_SETPOINT:
    "Desired setpoint for feedback loop controlling z-height of probe.",
    MicroscopeParameter.ZCTRL_PGAIN:
    "Desired gain for proportional component of feedback loop controlling " +
    "z-height of probe.",
    MicroscopeParameter.ZCTRL_IGAIN:
    "Desired gain for integral component of feedback loop controlling " +
    "z-height of probe.",
    MicroscopeParameter.ZCTRL_EGAIN:
    "Desired gain for error fed into feedback loop controlling " +
    "z-height of probe.",

    # Sample Slope Correction
    MicroscopeParameter.SAMPLE_SLOPE_X:
    "Slope of sample plane along x-axis.",

    MicroscopeParameter.SAMPLE_SLOPE_Y:
    "Slope of sample plane along y-axis.",

    # Probe / Scan Parameters
    MicroscopeParameter.SCAN_SPEED:
    "Speed at which scanning occurs.",
    MicroscopeParameter.MOVING_SPEED:
    "Speed at which probe movement occurs.",

    # Probe Parameters
    MicroscopeParameter.PROBE_POS_X:
    "Position of microscope probe on surface, x-dimension.",
    MicroscopeParameter.PROBE_POS_Y:
    "Position of microscope probe on surface, y-dimension.",

    # Other
    MicroscopeParameter.TIP_BIAS_VOLTAGE:
    "Bias voltage applied to the tip.",
}

# ----- Helper lists of Microscope Parameters ----- #
SCAN_PARAMS = [MicroscopeParameter.SCAN_TOP_LEFT_X,
               MicroscopeParameter.SCAN_TOP_LEFT_Y,
               MicroscopeParameter.SCAN_SIZE_X,
               MicroscopeParameter.SCAN_SIZE_Y,
               MicroscopeParameter.SCAN_RESOLUTION_X,
               MicroscopeParameter.SCAN_RESOLUTION_Y,
               MicroscopeParameter.SCAN_ANGLE]

ZCTRL_PARAMS = [MicroscopeParameter.ZCTRL_SETPOINT,
                MicroscopeParameter.ZCTRL_PGAIN,
                MicroscopeParameter.ZCTRL_IGAIN,
                MicroscopeParameter.ZCTRL_EGAIN]
# Attrib names from feedback.proto
ZCTRL_ATTRIB_STRS = ['setPoint', 'proportionalGain', 'integralGain',
                     'errorGain']

PROBE_POS_PARAMS = [MicroscopeParameter.PROBE_POS_X,
                    MicroscopeParameter.PROBE_POS_Y]


class ParameterError(Exception):
    """Translator failed at getting or setting a parameter."""

    pass


class ParameterNotSupportedError(Exception):
    """The requested parameter is not supported by this Microscope."""

    pass


class ParameterConfigurationError(Exception):
    """There was an error with the config file tied to requested parameter."""

    pass


# ----- Helpers for handling params config ----- #
@dataclass
class ParameterInfo:
    """Holds microscope-specific info from a parameters config file."""

    uuid: str  # Microscope-specific param UUID
    unit: str  # Microscope units for this param
    range: tuple[Any]  # Acceptable range for this param.
    type: Any  # A sample value of the type of this param, for type handling


@dataclass
class ParameterMethods:
    """Holds microscope-specific get/set from a parameters config file."""

    # NOTE: Here, we provide 'Any' instead of ParameterHandler for the
    # actual Callable arguments. This is due to the fact that we
    # declare ParameterHandler *after* this.
    # TODO: Think about a better way to do this...

    # In: ParameterHandler, val, curr_units
    # Out: None
    setter: Callable[[Any, str, str], None] | None

    # In: ParameterHandler
    # Out: val
    getter: Callable[[Any], Any] | None


def create_parameter_info(param_dict: dict) -> ParameterInfo:
    """Create ParameterInfo from a param_dict (from params config).

    Args:
        param_dict: dict for a particular parameter, obtained from
            params_config.
    Returns:
        ParameterInfo instance.
    """
    vals = []
    for key in ParameterInfo.__annotations__.keys():
        vals.append(param_dict[key] if key in param_dict else None)
    return ParameterInfo(*vals)


def create_parameter_methods(param_dict: dict) -> ParameterMethods:
    """Create ParameterMethods from a param_dict (from params config).

    Attempts to import setter and getter methods for a param dict.

    Args:
        param_dict: dict for a particular parameter, obtained from
            params_config.

    Returns:
        ParameterMethods instance.

    Raises:
        ModuleNotFoundError if module could not be found when trying
            import.
    """
    methods = []
    for key in ParameterMethods.__annotations__.keys():
        # Try to import method if in param_dict, else pass None.
        methods.append(import_from_string(param_dict[key])
                       if key in param_dict else None)
    return ParameterMethods(*methods)


# ----- Main handler class ----- #
class ParameterHandler(metaclass=ABCMeta):
    """Handles getting/setting various parameters for a microscope.

    This class simplifies getting/setting parameters to/from a microscope.
    Rather than having a large number of custom methods, it simplifies it by
    using a config file mechanism to:
    - map generic parameter names to microscope-specific ones (uuid key);
    - ensure setting parameters are within microscope-safe ranges (range key);
    - indicating the microscope-specific units for each parameter (units key)
    - allowing custom getter/setter methods for a given parameter, if
    necessary.

    The logic assumes that the mechanism by which a parameter is set/get is
    consistent for all (or most) parameters, consisting of some scope-specific
    equivalent to 'get_param()' and 'set_param()'. These are implemented via
    the abstract get_param_spm() and set_param_spm() calls. When a set is
    called, this class will:
    0. Convert the fed value to the appropriate type (based on the 'type')
    parameter in ParameterInfo. This is because vals are fed as str.
    1. Convert the generic MicroscopeParameter to the scope-specific string.
    2. Convert the fed value to the units expected by the microscope.
    3. Ensure the converted value is within the permitted range.
    4. Send the request out to the microscope.

    When getting, it will perform (1) and (4), returning the values along
    with the microscope units involved.

    However, we understand that not all parameters can be so easily mapped
    to a simple set/get. For example, it may be that a generic parameter
    does not map one-to-one to the scope-specific logic, and thus some form of
    extra conversion needs to be performed. For these cases, explicit set/get
    callables can be provided in the config file (getter/setter keys). These
    methods are called with the same expected input args and output args as
    get_param() / set_param(), *except* that the MicroscopeParameter itself
    is prepended in the input arguments. See ParameterMethods for the call
    format of the getter and setter methods.

    For example, a params config containing two simple params could be:

        ['scan-top-left-x']
        'uuid': 'TL_X'
        'units' 'nm'

        ['scan-angle']
        'setter': 'my_file.get_scan_angle'
        'getter': 'my_file.set_scan_angle'

    In this example, the 'scan-angle' parameter requires custom methods, which
    in this case are in a module my_file.py. The 'scan-top-left-x' parameter use
    uses get/set_param_spm() in this case.

    Attributes:
        param_infos: a dict of key:val pairs consisting of
            generic_param:ParameterInfo. For a given generic_param, its
            ParameterInfo will only be added if it at least has a 'uuid' and
            'type'.
        param_methods: a dict of key:val pairs consisting of
            generic_param:ParameterMethods. For a given generic_param, its
            ParameterMethods will only be added if it has both a setter and a
            getter defined.
    """

    def __init__(self, params_config_path: str):
        """Init class, loading params config for these purposes."""
        self.param_infos = {}
        self.param_methods = {}
        self._load_config_build_params(params_config_path)

    @abstractmethod
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

    @abstractmethod
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

    def _load_config_build_params(self, params_config_path: str):
        with open(params_config_path, 'rb') as file:
            params_config = tomli.load(file)
            self._build_param_infos_methods(params_config)

    def _build_param_infos_methods(self, params_config: dict):
        for key, val in params_config.items():
            if isinstance(val, dict):
                # Ceck if we have our own set/get methods
                param_methods = create_parameter_methods(val)

                if param_methods.setter or param_methods.getter:
                    no_setter_or_getter = (
                        'setter' if not param_methods.setter
                        else 'getter' if not param_methods.getter
                        else None)
                    if no_setter_or_getter:
                        logger.warning(f'For parameter {key}'
                                       f', {no_setter_or_getter} not '
                                       'provided! Continuing.')

                    logger.debug('Custom setter/getter provided for ' +
                                 f'parameter {key}. Using.')
                    self.param_methods[key] = param_methods

                # Check if we have needed parameter info
                param_info = create_parameter_info(val)

                if param_info.uuid and param_info.type:
                    self.param_infos[key] = param_info

                if (key not in self.param_methods and
                        key not in self.param_infos):
                    msg = (f"Parameter {key} provided without either set/" +
                           "get methods or both 'type' and 'uuid' attributes.")
                    logger.error(msg)
                    raise ParameterConfigurationError(msg)

    def _get_param_info(self, generic_param: MicroscopeParameter
                        ) -> ParameterInfo:
        """Get ParameterInfo with error handling."""
        if generic_param not in self.param_infos:
            msg = f'Parameter {generic_param} not found in params config.'
            logger.error(msg)
            raise ParameterNotSupportedError(msg)
        return self.param_infos[generic_param]

    def _get_param_methods(self, generic_param: MicroscopeParameter
                           ) -> ParameterMethods:
        """Get ParameterInfo (None if not found)."""
        if generic_param not in self.param_methods:
            return None
        return self.param_methods[generic_param]

    def get_units(self, generic_param: MicroscopeParameter) -> str:
        """Get the scope-specific units for the provided parameter.

        Args:
            generic_param: MicroscopeParameter we want to get.

        Returns:
            Units as str.

        Raises:
            ParameterNotSupportedError if the parameter was not found
                in the params_config dict.
        """
        return self._get_param_info(generic_param).unit

    def get_param(self, generic_param: MicroscopeParameter) -> Any:
        """Get the current value for the provided parameter.

        Args:
            generic_param: MicroscopeParameter we want to get.

        Returns:
            Current value.

        Raises:
            - ParameterNotSupportedError if the parameter was not found
                in the params_config dict.
            - ParameterError if getting the parameter fails.
        """
        methods = self._get_param_methods(generic_param)
        if methods and methods.getter:
            return methods.getter(self)

        uuid = self._get_param_info(generic_param).uuid
        return self.get_param_spm(uuid)

    def set_param(self, generic_param: MicroscopeParameter, val: str,
                  curr_unit: str = None):
        """Convert a value to appropriate units and set it.

        Args:
            generic_param: MicroscopeParameter we want to set.
            val: value to set it to, in str format (as it is sent in
                ParameterMsg).
            curr_unit: unit of provided value. optional.

        Raises:
            - ConversionError if the method failed to convert to the
                requested units.
            - ParameterNotSupportedError if the parameter was not found
                in the params_config dict.
            - ParameterError if the parameter could not be set.
        """
        methods = self._get_param_methods(generic_param)
        if methods and methods.setter:
            methods.setter(self, val, curr_unit)
            return

        param_info = self._get_param_info(generic_param)
        val = _correct_val_for_sending(val, param_info, generic_param,
                                       curr_unit)
        self.set_param_spm(param_info.uuid, val)

    def get_param_list(self, generic_params: list[MicroscopeParameter]
                       ) -> list[Any]:
        """Get params for a list of provided parameters."""
        return [self.get_param(param) for param in generic_params]

    def set_param_list(self, generic_params: list[MicroscopeParameter],
                       vals: list[str], curr_units: tuple[str | None]):
        """Convert a list of values to microscope units and set them.

        This differs from individual set_param calls in that we validate
        all conversions can be done before trying any sets.

        Args:
            generic_param: MicroscopeParameters we wish to set.
            vals: values to set them to in str format (as they are sent in
                ParameterMsg).
            curr_units: units of provided values. optional.

        Raises:
            - ConversionError if fails to convert one of the vals to
                requested units.
            - ParameterNotSupportedError if if one of the parameters was not
                found in the params_config dict.
            - ParameterError if one of the parameters could not be set.
        """
        spm_params = []
        spm_vals = []
        for generic_param, val, curr_unit in zip(generic_params, vals,
                                                 curr_units):
            param_info = self._get_param_info(generic_param)
            val = _correct_val_for_sending(val, param_info, generic_param,
                                           curr_unit)
            spm_params.append(param_info.uuid)
            spm_vals.append(_cap_val_in_range(val, param_info.range,
                                              generic_param))

        for spm_param, spm_val in zip(spm_params, spm_vals):
            self.set_param_spm(spm_param, spm_val)


def _correct_val_for_sending(val: str, param_info: ParameterInfo,
                             generic_param: str, curr_unit: str
                             ) -> Any:
    """Typify, convert units, and cap val in range."""
    val = _typify_val(val, param_info.type)
    val = units.convert(val, curr_unit, param_info.unit)
    val = _cap_val_in_range(val, param_info.range, generic_param)
    return val


def _cap_val_in_range(val: Any, val_range: tuple[Any] | None,
                      generic_uuid: str) -> Any:
    """Cap value within range if range is provided."""
    if val_range and not val_range[0] <= val <= val_range[1]:
        old_val = val
        val = (val_range[0] if val < val_range[0] else val_range[1]
               if val > val_range[1] else val)
        logger.info(f'Trying to set {generic_uuid} with value {old_val}, ' +
                    f'which is outside of range {val_range}. Capping to {val}.')
    return val


def _typify_val(val: Any, sample_type: Any) -> Any:
    """Try to convert val to required type.

    We use sample_type to determine the type that val should be, and try to
    convert val to it and return it.

    Note that we only really support float, int, str, bool.

    Args:
        val: input value, to potentially convert.
        sample_type: random value of type we want.

    Returns:
        val after conversion to sample_type.

    Raises:
        ???
    """
    if isinstance(sample_type, float):
        return float(val)
    elif isinstance(sample_type, int):
        return int(val)
    elif isinstance(sample_type, str):
        return str(val)
    elif isinstance(sample_type, bool):
        return bool(val)

    msg = (f'Sample type {sample_type} is not of supported types for ' +
           'conversion - cannot convert val to it.')
    logger.error(msg)
    raise AttributeError(msg)
