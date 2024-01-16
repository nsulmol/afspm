"""Tests to validate the zmq-xop interface logic functions properly."""

import pytest
import logging

from afspm.components.device.controllers.asylum import xop


logger = logging.getLogger(__name__)


@pytest.fixture
def python_path():
    return "C:/Users/nsulmol/data/test"


@pytest.fixture
def igor_path():
    return "C:Users:nsulmol:data:test"


def test_igor_to_python_path_logic(python_path, igor_path):
    logger.info("Validating igor-to-python path conversion works.")
    conv_python_path = xop.convert_igor_path_to_python_path(igor_path)
    assert conv_python_path == python_path


def test_python_to_igor_path_logic(python_path, igor_path):
    logger.info("Validating python-to-igor path conversion works.")
    conv_igor_path = xop.convert_python_path_to_igor_path(python_path)
    assert conv_igor_path == igor_path


@pytest.fixture
def sample_method_call():
    return "GV"


@pytest.fixture
def sample_method_param():
    return "ScanSize"


# TODO: Increment message ID somehow??
@pytest.fixture
def expected_json():
    return ('{"version": 1, "messageID": "0", '
            '"CallFunction": {"name": "GV", "params": ["ScanSize"]}}')


def test_create_call_string(sample_method_call, sample_method_param,
                            expected_json):
    err_code, json_str = xop.create_call_string(sample_method_call,
                                                [sample_method_param])
    print(f"expected json: {expected_json}")
    print(f"calced json: {json_str}")
    assert json_str == expected_json


@pytest.fixture
def sample_response_str():
    return ('{"errorCode": {"value": 0}, "messageID": "1", "result": '
            '{"type": "variable", "value": 511.3}}')


@pytest.fixture
def exp_err_code():
    return 0


@pytest.fixture
def exp_msg_id():
    return "1"


@pytest.fixture
def exp_param():
    return 511.3


def test_parse_response_string(sample_response_str, exp_err_code,
                               exp_msg_id, exp_param):
    res_err_code, res_msg_id, res_param = xop.parse_response_string(
        sample_response_str)

    assert res_err_code == exp_err_code
    assert res_msg_id == exp_msg_id
    assert res_param == exp_param

    tmp_str = sample_response_str.replace(xop.ERROR_KEY, "banana")
    with pytest.raises(xop.XOPSyntaxError):
        xop.parse_response_string(tmp_str)

    tmp_str = sample_response_str.replace("variable", "wave")
    with pytest.raises(xop.XOPUnsupportedTypeError):
        xop.parse_response_string(tmp_str)
