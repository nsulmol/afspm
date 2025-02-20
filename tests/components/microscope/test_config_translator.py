"""Basic tests of ConfigTranslator logic."""

import logging
import pytest
import tomli
from typing import Any

from afspm.components.microscope.translator import MicroscopeError
from afspm.components.microscope.config_translator import ConfigTranslator
from afspm.components.microscope import params
from afspm.components.microscope import actions
from afspm.io.common import create_scan_params_2d, create_probe_position

from afspm.io.protos.generated import control_pb2
from afspm.io.protos.generated import scan_pb2
from afspm.io.protos.generated import feedback_pb2
from afspm.io.protos.generated import signal_pb2


logger = logging.getLogger(__name__)


@pytest.fixture
def param_config_str():
    return """
    # Physical Scan Parameters
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

    [scan-size-x]
    uuid = 'SIZE_X'
    unit = 'nm'
    type = 1.0
    range = [0.0, 2.0]

    [scan-size-y]
    uuid = 'SIZE_Y'
    unit = 'nm'
    type = 1.0
    range = [0.0, 2.0]

    [scan-angle]
    uuid = 'ROTATION'
    unit = 'degrees'
    type = 1
    range = [0, 360]

    # Digital Scan Parameters
    [scan-resolution-x]
    uuid = 'RES_X'
    unit = 'Hz'
    type = 1
    range = [0, 2000]

    [scan-resolution-y]
    uuid = 'RES_Y'
    unit = 'Hz'
    type = 1.0
    range = [0, 2000]

    # Feedback Parameters
    [zctrl-setpoint]
    uuid = 'Z_SP'
    type = 1.0

    [zctrl-pgain]
    uuid = 'CP'
    type = 1.0

    [zctrl-igain]
    uuid = 'CI'
    type = 1.0

    [zctrl-egain]
    uuid = 'CE'
    type = 1.0

    # Probe Parameters (note, same as top-left scan pos)
    [probe-pos-x]
    uuid = 'TL_X'
    unit = 'nm'
    type = 1.0
    range = [0.0, 2.0]

    [probe-pos-y]
    uuid = 'TL_Y'
    unit = 'nm'
    type = 1.0
    range = [0.0, 2.0]
    """


@pytest.fixture
def vals_dict():
    return {
        'TL_X': 2.0, 'TL_Y': 1.0,
        'SIZE_X': 1.0, 'SIZE_Y': 1.0, 'ROTATION': 0,
        'RES_X': 256, 'RES_Y': 256,
        'Z_SP': 0.5, 'CP': 1.0, 'CI': 1.0, 'CE': 1.0,
    }


@pytest.fixture
def action_config_str():
    return """
    start-scan = 'bananer'
    stop-scan = 'appler'
    """


def load_config(config_str: str) -> dict:
    return tomli.loads(config_str)


class MyParameterHandler(params.ParameterHandler):

    def __init__(self, params_config: dict, vals_dict: dict):
        """Different from parent in that we feed str rather than file path."""
        self.vals = vals_dict
        self.param_infos = {}
        self.param_methods = {}
        self._build_param_infos_methods(params_config)

    def get_param_spm(self, spm_uuid: str) -> Any:
        return self.vals[spm_uuid]

    def set_param_spm(self, spm_uuid: str, spm_val: Any):
        self.vals[spm_uuid] = spm_val


class MyStringActionHandler(actions.StringActionHandler):
    last_call_uuid = None

    def __init__(self, actions_config: dict):
        self.actions = {}
        self._build_actions(actions_config)

    def request_action_spm(self, spm_uuid: str):
        self.last_call_uuid = spm_uuid


class MyConfigTranslator(ConfigTranslator):
    def poll_scans(self) -> list[scan_pb2.Scan2d]:
        """Not implementing, not tested here."""
        raise MicroscopeError

    def poll_scope_state(self) -> scan_pb2.ScopeState:
        """Not implementing, not tested here."""
        raise MicroscopeError

    def poll_signal(self) -> signal_pb2.Signal1d:
        """Not implementing, not tested here."""
        raise MicroscopeError


def construct_translator(param_config_str, vals_dict, action_config_str):
    param_config = load_config(param_config_str)
    action_config = load_config(action_config_str)

    param_handler = MyParameterHandler(param_config, vals_dict)
    action_handler = MyStringActionHandler(action_config)
    config_translator = MyConfigTranslator('my_translator', None, None,
                                           param_handler,
                                           action_handler)
    return config_translator


def test_scan_params(param_config_str, vals_dict, action_config_str):
    logger.info('Test that scan_params set/get works as expected.')
    config_translator = construct_translator(param_config_str, vals_dict,
                                             action_config_str)

    exp_scan_params = create_scan_params_2d(top_left=(2.0, 1.0),
                                            size=(1.0, 1.0),
                                            length_units='nm',
                                            angular_units='degrees',
                                            data_shape=(256, 256),
                                            data_units='Hz',
                                            angle=0)

    curr_scan_params = config_translator.poll_scan_params()
    assert exp_scan_params == curr_scan_params

    exp_scan_params = create_scan_params_2d(
        top_left=(0.3, 0.75),
        size=(exp_scan_params.spatial.roi.size.x,
              exp_scan_params.spatial.roi.size.y),
        length_units=exp_scan_params.spatial.length_units,
        angular_units=exp_scan_params.spatial.angular_units,
        data_shape=(exp_scan_params.data.shape.x,
                    exp_scan_params.data.shape.y),
        data_units=exp_scan_params.data.units,
        angle=35)
    config_translator.on_set_scan_params(exp_scan_params)
    curr_scan_params = config_translator.poll_scan_params()
    assert exp_scan_params == curr_scan_params


def test_zctrl_params(param_config_str, vals_dict, action_config_str):
    logger.info('Test that zctrl set/get works as expected.')
    config_translator = construct_translator(param_config_str, vals_dict,
                                             action_config_str)

    # TODO: Decide on feedbackOn param and uncomment/update if using.
    exp_zctrl_params = feedback_pb2.ZCtrlParameters(  # feedbackOn=True,
        setPoint=0.5,
        proportionalGain=1.0,
        integralGain=1.0,
        errorGain=1.0)

    curr_zctrl_params = config_translator.poll_zctrl_params()
    assert exp_zctrl_params == curr_zctrl_params

    exp_zctrl_params = feedback_pb2.ZCtrlParameters(  # feedbackOn=False,
        setPoint=0.3,
        proportionalGain=0.3,
        integralGain=0.5,
        errorGain=0.1)
    config_translator.on_set_zctrl_params(exp_zctrl_params)
    curr_zctrl_params = config_translator.poll_zctrl_params()
    assert exp_zctrl_params == curr_zctrl_params


def test_probe_position(param_config_str, vals_dict, action_config_str):
    logger.info('Test that probe positioning work (individual ones).')
    config_translator = construct_translator(param_config_str, vals_dict,
                                             action_config_str)

    exp_probe_pos = create_probe_position(pos=(2.0, 1.0), units='nm')
    curr_probe_pos = config_translator.poll_probe_pos()
    assert exp_probe_pos == curr_probe_pos

    exp_probe_pos = create_probe_position(pos=(1.1, 1.2), units='nm')
    config_translator.on_set_probe_pos(exp_probe_pos)
    curr_probe_pos = config_translator.poll_probe_pos()
    assert exp_probe_pos == curr_probe_pos


def test_req_param(param_config_str, vals_dict, action_config_str):
    logger.info('Test that REQ_PARAM calls work (individual ones).')
    config_translator = construct_translator(param_config_str, vals_dict,
                                             action_config_str)

    logger.info('If we get a supported param, all is kosher.')
    param_msg = control_pb2.ParameterMsg(
        parameter=params.MicroscopeParameter.SCAN_TOP_LEFT_X)
    rep, recv_param_msg = config_translator.on_param_request(param_msg)
    assert rep == control_pb2.ControlResponse.REP_SUCCESS
    assert recv_param_msg.value == str(2.0)

    logger.info('If we set a supported param, all is kosher.')
    param_msg = control_pb2.ParameterMsg(
        parameter=params.MicroscopeParameter.SCAN_TOP_LEFT_X,
        value='1.1', units='nm')
    rep, recv_param_msg = config_translator.on_param_request(param_msg)
    assert rep == control_pb2.ControlResponse.REP_SUCCESS
    assert recv_param_msg.value == str(1.1)

    logger.info('If we get an unsupported param, we get a failure.')
    param_msg = control_pb2.ParameterMsg(
        parameter='hallooooo')
    rep, recv_param_msg = config_translator.on_param_request(param_msg)
    assert rep == control_pb2.ControlResponse.REP_PARAM_NOT_SUPPORTED
    assert recv_param_msg is None

    logger.info('If we set an unsupported param, we get a failure.')
    param_msg = control_pb2.ParameterMsg(
        parameter='hallooooo', value=str(1.0), units='yabadabadoo')
    rep, recv_param_msg = config_translator.on_param_request(param_msg)
    assert rep == control_pb2.ControlResponse.REP_PARAM_NOT_SUPPORTED
    assert recv_param_msg is None


def test_req_action(param_config_str, vals_dict, action_config_str):
    logger.info('Test that REQ_ACTION calls work (individual ones).')
    config_translator = construct_translator(param_config_str, vals_dict,
                                             action_config_str)

    logger.info('If we request a supported action, all is kosher.')
    action_msg = control_pb2.ActionMsg(
        action=actions.MicroscopeAction.START_SCAN)
    rep = config_translator.on_action_request(action_msg)
    assert rep == control_pb2.ControlResponse.REP_SUCCESS

    logger.info('If we request an unsupported action, we get a failure.')
    action_msg = control_pb2.ActionMsg(
        action='monkeywrench')
    rep = config_translator.on_action_request(action_msg)
    assert rep == control_pb2.ControlResponse.REP_ACTION_NOT_SUPPORTED
