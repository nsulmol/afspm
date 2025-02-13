"""Test the parameters logic (principally ParameterHandler)."""

import os
import sys
import math
import logging
import pytest
from typing import Any
import tomli

from afspm.components.microscope import params
from afspm.utils import units

logger = logging.getLogger(__name__)


# Add current path to PATH for tests
path = os.path.dirname(os.path.realpath(__file__)) + os.sep
sys.path.append(path)


SET_VALS = {
    params.MicroscopeParameter.SCAN_TOP_LEFT_X: 3.0,
    params.MicroscopeParameter.SCAN_TOP_LEFT_Y: 2.0,
}


# -------------------- Methods -------------------- #
def good_getter_x(param: params.MicroscopeParameter) -> Any:
    return SET_VALS[params.MicroscopeParameter.SCAN_TOP_LEFT_X]


def good_getter_y(param: params.MicroscopeParameter) -> Any:
    return SET_VALS[params.MicroscopeParameter.SCAN_TOP_LEFT_Y]


def good_setter_x(param: params.MicroscopeParameter,
                  val: Any, unit: str):
    global SET_VALS
    SET_VALS[params.MicroscopeParameter.SCAN_TOP_LEFT_X] = val


def good_setter_y(param: params.MicroscopeParameter,
                  val: Any, unit: str):
    global SET_VALS
    SET_VALS[params.MicroscopeParameter.SCAN_TOP_LEFT_Y] = val


def load_config(config_str: str) -> dict:
    return tomli.loads(config_str)


class MyParameterHandler(params.ParameterHandler):
    vals = {'TL_X': 2.0, 'TL_Y': 1.0}

    def __init__(self, params_config: dict):
        """Different from parent in that we feed str rather than file path."""
        self.param_infos = {}
        self.param_methods = {}
        self._build_param_infos_methods(params_config)

    def get_param_spm(self, spm_uuid: str) -> Any:
        return self.vals[spm_uuid]

    def set_param_spm(self, spm_uuid: str, spm_val: Any):
        self.vals[spm_uuid] = spm_val


# -------------------- Fixtures -------------------- #
@pytest.fixture
def config_set_get():
    return """
    [scan-top-left-x]
    getter = 'test_params.good_getter_x'
    setter = 'test_params.good_setter_x'

    [scan-top-left-y]
    getter = 'test_params.good_getter_y'
    setter = 'test_params.good_setter_y'
    """


@pytest.fixture
def config_param_info():
    return """
    [scan-top-left-x]
    uuid = 'TL_X'
    unit = 'nm'
    type = 1.0
    range = [0.0, 2.0]

    [scan-top-left-y]
    uuid = 'TL_Y'
    unit = 'nm'
    type = 1.0
    range = [0.0, 2.0]
    """


@pytest.fixture
def config_both():
    return """
    [scan-top-left-x]
    uuid = 'TL_X'
    unit = 'nm'
    type = 1.0
    range = [0.0, 2.0]
    getter = 'test_params.good_getter_x'
    setter = 'test_params.good_setter_x'


    [scan-top-left-y]
    uuid = 'TL_Y'
    unit = 'nm'
    type = 1.0
    range = [0.0, 2.0]
    getter = 'test_params.good_getter_y'
    setter = 'test_params.good_setter_y'
    """


# TODO: Remove uuid, remove type,, remove range, etc..


# -------------------- Tests -------------------- #
def test_param_not_in_configs(config_param_info):
    logger.info('Should throw error if param not in configs.')
    params_config = load_config(config_param_info)
    param_handler = MyParameterHandler(params_config)
    key = params.MicroscopeParameter.SCAN_ANGLE

    with pytest.raises(params.ParameterNotSupportedError):
        param_handler.get_param(key)


def test_param_missing_info_attrs(config_param_info):
    logger.info('Should throw error if missing uuid and/or type.')

    key = params.MicroscopeParameter.SCAN_TOP_LEFT_X

    logger.info('Trying without uuid.')
    params_config = load_config(config_param_info)
    params_config[key].pop('uuid', None)

    with pytest.raises(params.ParameterConfigurationError):
        param_handler = MyParameterHandler(params_config)

    logger.info('Trying without type.')
    params_config = load_config(config_param_info)
    params_config[key].pop('type', None)

    with pytest.raises(params.ParameterConfigurationError):
        param_handler = MyParameterHandler(params_config)

    logger.info('Trying all of the things.')
    params_config = load_config(config_param_info)
    param_handler = MyParameterHandler(params_config)


def test_param_missing_setter_getter(config_set_get):
    logger.info('Should throw error if either of set/get missing.')

    key = params.MicroscopeParameter.SCAN_TOP_LEFT_X

    logger.info('Trying without setter.')
    params_config = load_config(config_set_get)
    params_config[key].pop('setter', None)

    with pytest.raises(params.ParameterConfigurationError):
        param_handler = MyParameterHandler(params_config)

    logger.info('Trying without getter.')
    params_config = load_config(config_set_get)
    params_config[key].pop('getter', None)

    with pytest.raises(params.ParameterConfigurationError):
        param_handler = MyParameterHandler(params_config)

    logger.info('Trying without both.')
    params_config = load_config(config_set_get)
    params_config[key].pop('getter', None)
    params_config[key].pop('setter', None)

    with pytest.raises(params.ParameterConfigurationError):
        param_handler = MyParameterHandler(params_config)

    logger.info('Trying with both.')
    params_config = load_config(config_set_get)
    param_handler = MyParameterHandler(params_config)


def test_choose_setter_getter_priority(config_both, config_param_info):
    logger.info('If a param contains both set/get and info, defaults to ' +
                'set/get.')
    params_config = load_config(config_both)
    param_handler = MyParameterHandler(params_config)
    for key in [params.MicroscopeParameter.SCAN_TOP_LEFT_X,
                params.MicroscopeParameter.SCAN_TOP_LEFT_Y]:
        logger.info(f'Confirming we get the getter value for {key}.')
        val = param_handler.get_param(key)
        assert val == SET_VALS[key]

        logger.info(f'Confirming we set the setter value for {key}.')
        new_val = 0.0
        param_handler.set_param(key, new_val, 'nm')
        assert new_val == SET_VALS[key]

    logger.info('If a param contains only info, we use that.')
    params_config = load_config(config_param_info)
    param_handler = MyParameterHandler(params_config)
    for key in [params.MicroscopeParameter.SCAN_TOP_LEFT_X,
                params.MicroscopeParameter.SCAN_TOP_LEFT_Y]:
        logger.info(f'Confirming we get the class value for {key}.')
        val = param_handler.get_param(key)
        spm_key = param_handler._get_param_info(key).uuid
        assert val == MyParameterHandler.vals[spm_key]

        logger.info(f'Confirming we set the class value for {key}.')
        new_val = 1.1
        param_handler.set_param(key, new_val, 'nm')
        val = param_handler.get_param(key)
        assert new_val == val  #MyParameterHandler.vals[spm_key]


def test_range_works(config_both, config_param_info):
    logger.info('Units are kept within range when using set_param_spm.')
    params_config = load_config(config_param_info)
    param_handler = MyParameterHandler(params_config)

    key = params.MicroscopeParameter.SCAN_TOP_LEFT_X
    for (bad_val, expected_val) in zip([-1.0, 50.0], [0.0, 2.0]):
        param_handler.set_param(key, bad_val, 'nm')
        val = param_handler.get_param(key)
        assert val == expected_val

    logger.info('Range checking is not done with setter/getter.')
    params_config = load_config(config_both)
    param_handler = MyParameterHandler(params_config)

    key = params.MicroscopeParameter.SCAN_TOP_LEFT_X
    for bad_val in [-1.0, 50.0]:
        param_handler.set_param(key, bad_val, 'nm')
        val = param_handler.get_param(key)
        assert val == bad_val


def test_typify_works(config_param_info):
    logger.info('Check that typifying works as expected.')

    params_config = load_config(config_param_info)
    param_handler = MyParameterHandler(params_config)

    logger.info('Int gets converted to float properly.')
    key = params.MicroscopeParameter.SCAN_TOP_LEFT_X
    param_handler.set_param(key, 1, 'nm')
    val = param_handler.get_param(key)
    assert val == 1.0

    logger.info('Str gets converted to float properly.')
    key = params.MicroscopeParameter.SCAN_TOP_LEFT_X
    param_handler.set_param(key, '1', 'nm')
    val = param_handler.get_param(key)
    assert val == 1.0


def test_conversion(config_param_info):
    logger.info('Converting withiin ParameterHandler works.')

    params_config = load_config(config_param_info)
    param_handler = MyParameterHandler(params_config)

    key = params.MicroscopeParameter.SCAN_TOP_LEFT_X
    param_handler.set_param(key, 10, 'angstrom')
    val = param_handler.get_param(key)
    assert math.isclose(val, 1.0)

    logger.info('Trying to convert with unitless when microscope has units' +
                'throws an error.')
    with pytest.raises(units.ConversionError):
        param_handler.set_param(key, 1.0, None)
