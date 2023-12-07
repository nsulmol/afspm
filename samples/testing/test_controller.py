"""This test suite exists to validate device controller functionality.

In order to run it, you must:
1. Start up the afspmcon, via a call like the following:
    spawn config.toml --components_to_spawn ['afspmcon']
2. Start up the device controller you wish to test. This startup will be
implementation-specific (look at the readme for your controller).
3. Run these tests:
    pytest $PATH_TO_TEST/test_controller.py --config_path $CONFIG_FILE_PATH
Note the default config path is './config.toml'.

Your config file contains the parameters for our tests. See sample_config.toml
for guidance.

Ideally, you should set your controller's scan parameters to scan quickly
before running these tests. For example, increase your scan speed and
decrease your ROI size to pass the test more quickly.
"""

import logging
import copy
import time
import tomli
import pytest
import zmq

from google.protobuf.message import Message

from afspm.io.pubsub.subscriber import Subscriber
from afspm.io.pubsub.logic import cache_logic as cl
from afspm.io.control.client import ControlClient, AdminControlClient

from afspm.components.afspm.component import AfspmComponent
from afspm.components.afspm.controller import AfspmController

from afspm.io.protos.generated import scan_pb2
from afspm.io.protos.generated import control_pb2
from afspm.io.protos.generated import feedback_pb2


logger = logging.getLogger(__name__)


def pytest_addoption(parser):
    parser.addoption("config_path", action="store", default="./config.toml",
                     help="Path to config file, from which to load params.")


# -------------------- Fixtures -------------------- #
@pytest.fixture(scope="session")
def config_path(request):
    return request.config.get_option("--config_path")


@pytest.fixture(scope="session")
def config_dict(config_path):
    return tomli.load(config_path)


# --- General / Urls --- #
@pytest.fixture
def ctx():
    return zmq.Context.instance()


@pytest.fixture(scope="module")
def component_name():
    return "TestComponent"


@pytest.fixture(scope="module")
def timeout_ms():
    return config_dict['timeout_ms']


@pytest.fixture(scope="module")
def scan_wait_ms():
    return config_dict['scan_wait_ms']


@pytest.fixture(scope="module")
def psc_url():
    return config_dict['psc_url']


@pytest.fixture(scope="module")
def router_url():
    return config_dict['router_url']

@pytest.fixture(scope="module")
def control_mode():
    return control_pb2.ControlMode.CM_AUTOMATED


@pytest.fixture(scope="module")
def default_control_state(control_mode):
    cs = control_pb2.ControlState()
    cs.control_mode = control_mode
    return cs


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
    return [cl.CacheLogic.get_envelope_for_proto(feedback_pb2.ZCtrlParameters)]


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


def sub_zctrl(ctx, topics_zctrl, timeout_ms, psc_url):
    return Subscriber(psc_url,
                      topics_to_sub=topics_zctrl,
                      poll_timeout_ms=timeout_ms)


@pytest.fixture
def client(ctx, component_name, router_url):
    return ControlClient(router_url, ctx, component_name)


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


# -------------------- Tests -------------------- #
def test_cancel_scan(client, default_control_state,
                     sub_scan, sub_scan_state, timeout_ms,
                     control_mode):
    logger.info("Validate we can start and cancel a scan.")
    logger.info("First, validate we *do not* have an initial scan (in the "
                "cache), and *do* have an initial scan state (SS_FREE).")
    startup_grab_control(client, control_mode)

    scan_state_msg = scan_pb2.ScanStateMsg(
        scan_state=scan_pb2.ScanState.SS_FREE)

    # Hack around, make poll short for this.
    tmp_timeout_ms = sub_scan.poll_timeout_ms
    sub_scan.poll_timeout_ms = timeout_ms
    assert not sub_scan.poll_and_store()
    assert_sub_received_proto(sub_scan_state,
                              scan_state_msg)
    sub_scan.poll_timeout_ms = tmp_timeout_ms  # Return to prior

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
    stop_client(client)


def test_scan_params(client, default_control_state,
                     sub_scan_params, control_mode):
    logger.info("Validate we can set scan parameters.")
    logger.info("First, validate we have initial scan params (from the "
                "cache).")
    startup_grab_control(client, control_mode)

    __, initial_params = sub_scan_params.poll_and_store()
    assert initial_params

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
    __, last_params = sub_scan_params.poll_and_store()
    assert last_params == modified_params

    logger.info("Now, return to our initial parameters.")
    rep = client.set_scan_params(initial_params)
    assert rep == control_pb2.ControlResponse.REP_SUCCESS
    __, last_params = sub_scan_params.poll_and_store()
    assert last_params == initial_params

    stop_client(client)


def test_run_scan(client, default_control_state,
                  sub_scan, sub_scan_state, timeout_ms,
                  control_mode):
    logger.info("Validate we can start a scan, and receive one on finish.")
    logger.info("First, validate we *do not* have an initial scan (in the "
                "cache), and *do* have an initial scan state (SS_FREE).")
    startup_grab_control(client, control_mode)

    scan_state_msg = scan_pb2.ScanStateMsg(
        scan_state=scan_pb2.ScanState.SS_FREE)

    # Hack around, make poll short for this.
    tmp_timeout_ms = sub_scan.poll_timeout_ms
    sub_scan.poll_timeout_ms = timeout_ms
    assert not sub_scan.poll_and_store()
    assert_sub_received_proto(sub_scan_state,
                              scan_state_msg)
    sub_scan.poll_timeout_ms = tmp_timeout_ms  # Return to prior

    logger.info("Next, validate that we can start a scan and are notified "
                "scanning has begun.")
    rep = client.start_scan()
    scan_state_msg = scan_pb2.ScanStateMsg(
        scan_state=scan_pb2.ScanState.SS_SCANNING)
    assert rep == control_pb2.ControlResponse.REP_SUCCESS
    assert_sub_received_proto(sub_scan_state, scan_state_msg)

    logger.info("Lastly, wait for a predetermined 'long-enough' period, "
                "and validate the scan finishes.")
    assert sub_scan.poll_and_store()
    scan_state_msg.scan_state = scan_pb2.ScanState.SS_FREE
    assert_sub_received_proto(sub_scan_state, scan_state_msg)

    stop_client(client)


def test_handle_zctrl(client, default_control_state,
                      sub_zctrl, timeout_ms,
                      control_mode):
    logger.info("Validate we recieve and can set ZCtrlParams.")

    startup_grab_control(client, control_mode)

    logger.info("First, ensure we receive initial ZCtrlParams.")
    __, initial_params = sub_zctrl.poll_and_store()
    assert initial_params

    modified_params = copy.deepcopy(initial_params)
    modified_params.proportionalGain *= 1.1
    modified_params.integralGain *= 1.1

    logger.info("Next, set new ZCtrlParams. We expect a success.")
    rep = client.set_zctrl_params(modified_params)
    assert rep == control_pb2.ControlResponse.REP_SUCCESS

    logger.info("Next, validate that our subscriber receives these new "
                "params.")
    __, last_params = sub_zctrl.poll_and_store()
    assert last_params == modified_params

    logger.info("Now, return to our initial parameters.")
    rep = client.set_zctrl_params(initial_params)
    assert rep == control_pb2.ControlResponse.REP_SUCCESS
    __, last_params = sub_zctrl.poll_and_store()
    assert last_params == initial_params

    stop_client(client)
