"""This test suite exists to validate device controller functionality."""

import logging
import copy
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


logger = logging.getLogger(__name__)


# -------------------- Fixtures -------------------- #
# --- General / Urls --- #
@pytest.fixture
def ctx():
    return zmq.Context.instance()


@pytest.fixture
def component_name():
    return "TestComponent"


@pytest.fixture
def timeout_ms():
    return 5000


@pytest.fixture
def scan_wait_ms():
    return 180000


@pytest.fixture
def psc_url():
    return "tcp://127.0.0.1:7778"


@pytest.fixture
def server_url():
    return "tcp://127.0.0.1:7777"


@pytest.fixture(scope="module")
def default_control_state():
    cs = control_pb2.ControlState()
    cs.control_mode = control_pb2.ControlMode.CM_AUTOMATED
    return cs


@pytest.fixture(scope="module")
def topics_scan():
    return [cl.CacheLogic.get_envelope_for_proto(scan_pb2.Scan2d())]


@pytest.fixture(scope="module")
def topics_scan_params():
    return [cl.CacheLogic.get_envelope_for_proto(scan_pb2.ScanParameters2d())]


@pytest.fixture(scope="module")
def topics_scan_state():
    return [cl.CacheLogic.get_envelope_for_proto(scan_pb2.ScanStateMsg())]


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
def client(ctx, component_name, server_url):
    return ControlClient(server_url, ctx, component_name)


# -------------------- Helper Methods -------------------- #
def assert_sub_received_proto(sub: Subscriber, proto: Message):
    """Confirm a message is received by a subscriber."""
    assert sub.poll_and_store()
    assert len(sub.cache[cl.CacheLogic.get_envelope_for_proto(proto)]) == 1
    assert (sub.cache[cl.CacheLogic.get_envelope_for_proto(proto)][0]
            == proto)


# -------------------- Tests -------------------- #
def test_cancel_scan(client, default_control_state, component_name,
                     sub_scan, sub_scan_state, timeout_ms):
    logger.info("Validate we can start and cancel a scan.")
    logger.info("First, validate we *do not* have an initial scan (in the "
                "cache), and *do* have an initial scan state (SS_FREE).")
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


def test_scan_params(client, default_control_state, component_name,
                     sub_scan_params):
    logger.info("Validate we can set scan parameters.")
    logger.info("First, validate we have initial scan params (from the "
                "cache).")
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


def test_scan(client, default_control_state, component_name,
              sub_scan, sub_scan_state, timeout_ms):
    logger.info("Validate we can start a scan, and receive one on finish.")
    logger.info("First, validate we *do not* have an initial scan (in the "
                "cache), and *do* have an initial scan state (SS_FREE).")

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
    scan_state_msg.scan_state = scan_pb2.ScanState.SS_FREE
    assert_sub_received_proto(sub_scan_state, scan_state_msg)
    assert sub_scan.poll_and_store()
