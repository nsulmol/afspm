"""This test suite exists to validate device controller functionality.

In order to run it, you must:
1. Start up the afspmcon, via a call like the following:
    poetry run spawn config.toml --components_to_spawn ['afspmcon']
2. Start up the device controller you wish to test. This startup will be
implementation-specific (look at the readme for your controller).
3. Run these tests:
    poetry run pytest $PATH_TO_TEST/test_controller.py --config_path $CONFIG_FILE_PATH
Note the default config path is './config.toml'.

Your config file contains the parameters for our tests. See sample_config.toml
for guidance.

Ideally, you should set your controller's scan parameters to scan quickly
before running these tests. For example, increase your scan speed and
decrease your ROI size to pass the test more quickly. This can be accomplished
by including SCAN_SPEED_KEY, PHYS_SIZE_KEY, and DATA_SHAPE_KEY keys in your
config, with appropriate values. See these variables below, or look at
sample_config.toml.
"""

from typing import Optional
import logging
import copy
import time
import tomli
import pytest
import zmq

from google.protobuf.message import Message

from afspm.io import common
from afspm.io.pubsub.subscriber import Subscriber
from afspm.io.pubsub.logic import cache_logic as cl
from afspm.io.control.client import ControlClient

from afspm.components.device import params
from afspm.utils.log import LOGGER_ROOT
from afspm.utils import units
from afspm.utils.protobuf import check_equal

from afspm.io.protos.generated import scan_pb2
from afspm.io.protos.generated import control_pb2
from afspm.io.protos.generated import feedback_pb2


logger = logging.getLogger(LOGGER_ROOT + '.samples.testing.test_controller.' +
                           __name__)


# Constants for config
SCAN_SPEED_KEY = 'scan_speed_nm_s'
PHYS_SIZE_KEY = 'phys_size_nm'
DATA_SHAPE_KEY = 'data_shape'
REQUEST_TIMEOUT_MS_KEY = 'request_timeout_ms'
# The relative tolerance used to compare set/get params (when float).
FLOAT_TOLERANCE_KEY = 'float_tolerance'


# -------------------- Fixtures -------------------- #
@pytest.fixture(scope="session")
def config_dict(config_path):
    with open(config_path, 'rb') as file:
        return tomli.load(file)


# --- General / Urls --- #
@pytest.fixture
def ctx():
    return zmq.Context.instance()


@pytest.fixture(scope="module")
def component_name():
    return "TestComponent"


@pytest.fixture(scope="module")
def timeout_ms(config_dict):
    return config_dict['timeout_ms']


@pytest.fixture(scope="module")
def scan_wait_ms(config_dict):
    return config_dict['scan_wait_ms']


@pytest.fixture(scope="module")
def psc_url(config_dict):
    return config_dict['psc_url']


@pytest.fixture(scope="module")
def router_url(config_dict):
    return config_dict['router_url']


@pytest.fixture(scope="module")
def control_mode():
    return control_pb2.ControlMode.CM_AUTOMATED


@pytest.fixture(scope="module")
def default_control_state(control_mode):
    cs = control_pb2.ControlState()
    cs.control_mode = control_mode
    return cs


@pytest.fixture(scope="module")
def request_timeout_ms(config_dict):
    if REQUEST_TIMEOUT_MS_KEY in config_dict:
        return config_dict[REQUEST_TIMEOUT_MS_KEY]
    return common.REQUEST_TIMEOUT_MS


@pytest.fixture(scope="module")
def float_tolerance(config_dict):
    if FLOAT_TOLERANCE_KEY in config_dict:
        return config_dict[FLOAT_TOLERANCE_KEY]
    return 1e-09  # Standard from math.isclose()


# Note: topics use 'base' CacheLogic, so we catch all messages of each
# type (even though our PSC is using PBCScanLogic to set the envelopes).
@pytest.fixture(scope="module")
def topics_scan():
    return [cl.CacheLogic.get_envelope_for_proto(scan_pb2.Scan2d())]


@pytest.fixture(scope="module")
def topics_scan_params():
    return [cl.CacheLogic.get_envelope_for_proto(scan_pb2.ScanParameters2d())]


@pytest.fixture(scope="module")
def topics_scan_state():
    return [cl.CacheLogic.get_envelope_for_proto(scan_pb2.ScanStateMsg())]


@pytest.fixture(scope="module")
def topics_zctrl():
    return [cl.CacheLogic.get_envelope_for_proto(feedback_pb2.ZCtrlParameters())]


# --- I/O Classes (Subscribers, Clients) --- #
@pytest.fixture
def sub_scan(ctx, topics_scan, scan_wait_ms, psc_url):
    return Subscriber(psc_url,
                      topics_to_sub=topics_scan,
                      poll_timeout_ms=scan_wait_ms)


@pytest.fixture
def sub_scan_state(ctx, topics_scan_state, timeout_ms, psc_url):
    return Subscriber(psc_url,
                      topics_to_sub=topics_scan_state,
                      poll_timeout_ms=timeout_ms)


@pytest.fixture
def sub_scan_params(ctx, topics_scan_params, timeout_ms, psc_url):
    return Subscriber(psc_url,
                      topics_to_sub=topics_scan_params,
                      poll_timeout_ms=timeout_ms)


@pytest.fixture
def sub_zctrl(ctx, topics_zctrl, timeout_ms, psc_url):
    return Subscriber(psc_url,
                      topics_to_sub=topics_zctrl,
                      poll_timeout_ms=timeout_ms)


@pytest.fixture
def client(ctx, component_name, router_url, request_timeout_ms):
    return ControlClient(router_url, ctx, component_name,
                         request_timeout_ms=2*request_timeout_ms)


# -------------------- Helper Methods -------------------- #
def assert_sub_received_proto(sub: Subscriber, proto: Message):
    """Confirm a message is received by a subscriber."""
    assert sub.poll_and_store()
    assert len(sub.cache[cl.CacheLogic.get_envelope_for_proto(proto)]) == 1
    assert (sub.cache[cl.CacheLogic.get_envelope_for_proto(proto)][0]
            == proto)


def stop_client(client: ControlClient):
    """Close client and wait a bit to ensure it closes.

    This appears to be an issue with ZMQ, where the client is not killed
    in time for the next test. This should not be an issue in the real world,
    as we should be restarting the full process whenever we set up a component.

    TODO: You should investigate this further. I hate magic sleeps :(.
    """
    client._close_client()
    del client  # Explicitly kill to avoid zmq weirdness.
    time.sleep(1)


def startup_grab_control(client: ControlClient,
                         control_mode: control_pb2.ControlMode.CM_AUTOMATED):
    """Request and get control of the device controller."""
    rep = client.request_control(control_mode)
    assert rep == control_pb2.ControlResponse.REP_SUCCESS


def end_test(client: ControlClient):
    """Release control at end of test."""
    rep = client.release_control()
    assert rep == control_pb2.ControlResponse.REP_SUCCESS


def assert_and_return_message(sub: Subscriber):
    """Assert that the subscriber received one message and return proto."""
    messages = sub.poll_and_store()
    assert len(messages) == 1
    return messages[0][1]


# ----- test_run_scan specific methods ----- #
def get_config_scan_speed(config_dict: dict,
                          client: ControlClient
                          ) -> Optional[tuple[float, float]]:
    """Determines if we are initing scan speed (for a faster scan).

    Checks if a desired scan speed was provided via the config, and the client
    supports setting scan speed. If so, we return the desired speed and
    the current/init speed the device controller has set.

    Args:
        config_dict: configuration dictionary for our tests.
        client: ControlClient we use to query the DeviceController.

    Returns:
        (float, float) tuple, containing (desired_val, init_val).
    """
    if SCAN_SPEED_KEY in config_dict:
        desired_param = config_dict[SCAN_SPEED_KEY]
        param_msg = control_pb2.ParameterMsg(
            parameter=params.DeviceParameter.SCAN_SPEED)
        rep, init_scan_msg = client.request_parameter(param_msg)
        if rep == control_pb2.ControlResponse.REP_SUCCESS:
            init_val_nm = units.convert(float(init_scan_msg.value),
                                        init_scan_msg.units, 'nm/s')
            return desired_param, init_val_nm
        msg = ("Controller failed setting/getting scan speed, "
               "returned response: ",
               common.get_enum_str(control_pb2.ControlResponse, rep))
        logger.error(msg)
        raise Exception(msg)
    return None


def get_config_phys_size_nm(config_dict: dict) -> Optional[list[float, float]]:
    """Returns desired physical size from config_dict, None if not set."""
    if PHYS_SIZE_KEY in config_dict:
        return config_dict[PHYS_SIZE_KEY]
    return None


def get_config_data_shape(config_dict: dict) -> Optional[list[int, int]]:
    """Returns desired data shape from config_dict, None if not set."""
    if DATA_SHAPE_KEY in config_dict:
        return config_dict[DATA_SHAPE_KEY]
    return None


def set_scan_speed(client: ControlClient, scan_speed_nm_s: float):
    param_msg = control_pb2.ParameterMsg(
        parameter=params.DeviceParameter.SCAN_SPEED,
        value=str(scan_speed_nm_s), units='nm/s')

    logger.info("Setting scan speed to desired: %s nm/s",
                scan_speed_nm_s)
    rep, __ = client.request_parameter(param_msg)
    assert rep == control_pb2.ControlResponse.REP_SUCCESS


def set_scan_params(client: ControlClient,
                    orig_params: scan_pb2.ScanParameters2d,
                    phys_size_nm: Optional[list[float, float]] = None,
                    data_shape: Optional[list[int, int]] = None):
    desired_params = copy.deepcopy(orig_params)
    if phys_size_nm:
        desired_params.spatial.roi.size.x = phys_size_nm[0]
        desired_params.spatial.roi.size.y = phys_size_nm[1]
        desired_params.spatial.units = 'nm'
    if data_shape:
        desired_params.data.shape.x = data_shape[0]
        desired_params.data.shape.y = data_shape[1]

    logger.info("Setting scan params to: %s",
                desired_params)
    rep = client.set_scan_params(desired_params)
    assert rep == control_pb2.ControlResponse.REP_SUCCESS


# -------------------- Tests -------------------- #
def test_cancel_scan(client, default_control_state,
                     sub_scan, sub_scan_state, timeout_ms,
                     control_mode):
    logger.info("Validate we can start and cancel a scan.")
    startup_grab_control(client, control_mode)

    logger.info("First, validate we *do not* have an initial scan (in the "
                "cache), and *do* have an initial scan state (SS_FREE).")
    scan_state_msg = scan_pb2.ScanStateMsg(
        scan_state=scan_pb2.ScanState.SS_FREE)

    # Hack around, make poll short for this.
    tmp_timeout_ms = sub_scan._poll_timeout_ms
    sub_scan._poll_timeout_ms = timeout_ms
    assert not sub_scan.poll_and_store()
    assert_sub_received_proto(sub_scan_state,
                              scan_state_msg)
    sub_scan._poll_timeout_ms = tmp_timeout_ms  # Return to prior

    logger.info("Next, validate that we can start a scan and are notified "
                "scanning has begun.")
    rep = client.start_scan()

    scan_state_msg = scan_pb2.ScanStateMsg(
        scan_state=scan_pb2.ScanState.SS_SCANNING)
    assert rep == control_pb2.ControlResponse.REP_SUCCESS
    assert_sub_received_proto(sub_scan_state,
                              scan_state_msg)

    logger.info("Next, cancel the scan before it has finished, and ensure "
                "we are notified it has been cancelled (via an interruption).")
    rep = client.stop_scan()
    assert rep == control_pb2.ControlResponse.REP_SUCCESS

    scan_state_msg = scan_pb2.ScanStateMsg(
        scan_state=scan_pb2.ScanState.SS_INTERRUPTED)
    assert_sub_received_proto(sub_scan_state,
                              scan_state_msg)

    logger.info("Lastly, ensure we are notified the controller is free and no "
                "scans were received.")
    scan_state_msg = scan_pb2.ScanStateMsg(
        scan_state=scan_pb2.ScanState.SS_FREE)
    assert_sub_received_proto(sub_scan_state,
                              scan_state_msg)

    assert not sub_scan_state.poll_and_store()
    end_test(client)
    stop_client(client)


def test_scan_params(client, default_control_state,
                     sub_scan_params, control_mode,
                     float_tolerance):
    logger.info("Validate we can set scan parameters.")
    startup_grab_control(client, control_mode)

    logger.info("First, validate we have initial scan params (from the "
                "cache).")
    initial_params = assert_and_return_message(sub_scan_params)
    modified_params = copy.deepcopy(initial_params)
    modified_params.spatial.roi.top_left.x *= 1.1
    modified_params.spatial.roi.top_left.y *= 1.1
    modified_params.spatial.roi.size.x *= 0.9
    modified_params.spatial.roi.size.y *= 0.9

    modified_params.data.shape.x -= 1
    modified_params.data.shape.y -= 1

    logger.info("Next, set new scan params. We expect a success.")
    rep = client.set_scan_params(modified_params)
    assert rep == control_pb2.ControlResponse.REP_SUCCESS

    logger.info("Next, validate that our subscriber receives these new "
                "params.")
    last_params = assert_and_return_message(sub_scan_params)

    assert check_equal(last_params, modified_params, float_tolerance)

    logger.info("Now, return to our initial parameters.")
    rep = client.set_scan_params(initial_params)
    assert rep == control_pb2.ControlResponse.REP_SUCCESS
    last_params = assert_and_return_message(sub_scan_params)

    assert check_equal(last_params, initial_params, float_tolerance)

    end_test(client)
    stop_client(client)


def test_run_scan(client, default_control_state,
                  sub_scan, sub_scan_state, sub_scan_params, timeout_ms,
                  control_mode, config_dict):
    logger.info("Validate we can start a scan, and receive one on finish.")
    startup_grab_control(client, control_mode)

    logger.info("First, check if we provided specific scan parameters "
                "(so the scan is not super long)")
    scan_speed_tuple = get_config_scan_speed(config_dict, client)
    orig_scan_speed = None
    if scan_speed_tuple:
        orig_scan_speed = scan_speed_tuple[1]
        set_scan_speed(client, scan_speed_tuple[0])

    desired_phys_size_nm = get_config_phys_size_nm(config_dict)
    desired_data_shape = get_config_data_shape(config_dict)
    orig_scan_params = None
    if desired_phys_size_nm or desired_data_shape:
        orig_scan_params = assert_and_return_message(sub_scan_params)
        set_scan_params(client, orig_scan_params, desired_phys_size_nm,
                        desired_data_shape)

    logger.info("Validate we *do not* have an initial scan (in the "
                "cache), and *do* have an initial scan state (SS_FREE).")
    scan_state_msg = scan_pb2.ScanStateMsg(
        scan_state=scan_pb2.ScanState.SS_FREE)

    # Hack around, make poll short for this.
    tmp_timeout_ms = sub_scan._poll_timeout_ms
    sub_scan._poll_timeout_ms = timeout_ms
    assert not sub_scan.poll_and_store()
    assert_sub_received_proto(sub_scan_state,
                              scan_state_msg)
    sub_scan._poll_timeout_ms = tmp_timeout_ms  # Return to prior

    logger.info("Validate that we can start a scan and are notified "
                "scanning has begun.")
    rep = client.start_scan()
    scan_state_msg = scan_pb2.ScanStateMsg(
        scan_state=scan_pb2.ScanState.SS_SCANNING)
    assert rep == control_pb2.ControlResponse.REP_SUCCESS
    assert_sub_received_proto(sub_scan_state, scan_state_msg)

    logger.info("Wait for a predetermined 'long-enough' period, "
                "and validate the scan finishes.")
    assert sub_scan.poll_and_store()
    scan_state_msg.scan_state = scan_pb2.ScanState.SS_FREE
    assert_sub_received_proto(sub_scan_state, scan_state_msg)

    if orig_scan_speed or orig_scan_params:
        logger.info("Reset scan settings to what they were before the test.")
        if orig_scan_speed:
            set_scan_speed(client, orig_scan_speed)
        if orig_scan_params:
            set_scan_params(client, orig_scan_params)

    end_test(client)
    stop_client(client)


def test_handle_zctrl(client, default_control_state,
                      sub_zctrl, timeout_ms,
                      control_mode, float_tolerance):
    logger.info("Validate we recieve and can set ZCtrlParams.")
    startup_grab_control(client, control_mode)

    logger.info("First, ensure we receive initial ZCtrlParams.")
    initial_params = assert_and_return_message(sub_zctrl)

    modified_params = copy.deepcopy(initial_params)
    modified_params.proportionalGain *= 1.1
    modified_params.integralGain *= 1.1

    logger.info("Next, set new ZCtrlParams. We expect a success.")
    rep = client.set_zctrl_params(modified_params)
    assert rep == control_pb2.ControlResponse.REP_SUCCESS

    logger.info("Next, validate that our subscriber receives these new "
                "params.")
    last_params = assert_and_return_message(sub_zctrl)
    assert check_equal(last_params, modified_params, float_tolerance)

    logger.info("Now, return to our initial parameters.")
    rep = client.set_zctrl_params(initial_params)
    assert rep == control_pb2.ControlResponse.REP_SUCCESS
    last_params = assert_and_return_message(sub_zctrl)
    assert check_equal(last_params, initial_params, float_tolerance)

    end_test(client)
    stop_client(client)
