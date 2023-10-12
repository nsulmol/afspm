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
    return 60000


@pytest.fixture
def pub_url():
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
def sub_scan(ctx, topics_scan, timeout_ms, pub_url):
    return Subscriber(pub_url,
                      topics_to_sub=topics_scan,
                      poll_timeout_ms=timeout_ms)


@pytest.fixture
def sub_scan_state(ctx, topics_scan_state, timeout_ms, pub_url):
    return Subscriber(pub_url,
                      topics_to_sub=topics_scan_state,
                      poll_timeout_ms=timeout_ms)


@pytest.fixture
def sub_scan_params(ctx, topics_scan, timeout_ms, pub_url):
    return Subscriber(pub_url,
                      topics_to_sub=topics_scan,
                      poll_timeout_ms=timeout_ms)


@pytest.fixture
def client(ctx, component_name, server_url):
    return ControlClient(server_url, ctx, component_name)


# --- Components --- #
@pytest.fixture
def afspm_component_scan(sub_scan, client, component_name, ctx):
    return AfspmComponent(component_name, sub_scan, client, ctx)

@pytest.fixture
def afspm_component_scan_params(sub_scan_params, client, component_name, ctx):
    return AfspmComponent(component_name, sub_scan_params, client, ctx)


# -------------------- Helper Methods -------------------- #
def assert_sub_received_proto(sub: Subscriber, proto: Message):
    """Confirm a message is received by a subscriber."""
    assert sub.poll_and_store()
    assert len(sub.cache[cl.CacheLogic.get_envelope_for_proto(proto)]) == 1
    assert (sub.cache[cl.CacheLogic.get_envelope_for_proto(proto)][0]
            == proto)


# -------------------- Tests -------------------- #
def test_cancel_scan(afspm_component_scan, default_control_state,
                     component_name, sub_scan_state):
    afspm_component = afspm_component_scan
    logger.info("Validate we can start and cancel a scan.")

    rep = afspm_component.control_client.start_scan()

    # Ensure we get a scanning message
    scan_state_msg = scan_pb2.ScanStateMsg(
        scan_state=scan_pb2.ScanState.SS_SCANNING)
    assert rep == control_pb2.ControlResponse.REP_SUCCESS
    assert_sub_received_proto(afspm_component.subscriber,
                              scan_state_msg)

    rep = afspm_component.control_client.stop_scan()

    # Ensure we get an interrupted message, a free message, and
    # no scan.
    assert rep == control_pb2.ControlResponse.REP_SUCCESS

    scan_state_msg = scan_pb2.ScanStateMsg(
        scan_state=scan_pb2.ScanState.SS_INTERRUPTED)
    assert_sub_received_proto(afspm_component.subscriber,
                              scan_state_msg)

    scan_state_msg = scan_pb2.ScanStateMsg(
        scan_state=scan_pb2.ScanState.SS_FREE)
    assert_sub_received_proto(afspm_component.subscriber,
                              scan_state_msg)

    assert not afspm_component.subscriber.poll_and_store()


def test_scan_params(afspm_component_scan_params, default_control_state,
                     component_name):
    afspm_component = afspm_component_scan_params
    logger.info("Validate we can set scan parameters.")

    # We should get an initial scan parameters
    initial_params = afspm_component.subscriber.poll_and_store()
    assert initial_params

    initial_params.spatial.roi.top_left.x *= 1.1
    initial_params.spatial.roi.top_left.y *= 1.1
    initial_params.spatial.roi.size.x *= 0.9
    initial_params.spatial.roi.size.y *= 0.9

    initial_params.data.shape.x -= 1
    initial_params.data.shape.y -= 1

    rep = afspm_component.control_client.set_scan_params(
        scan_pb2.ScanParameters2d())
    assert rep == control_pb2.ControlResponse.REP_SUCCESS

    # Expect to receive a reply within our timeout!
    last_params = afspm_component.subscriber.poll_and_store()
    assert last_params == initial_params


def test_scan(afspm_component_scan, default_control_state, component_name,
              sub_scan_state):
    afspm_component = afspm_component_scan
    logger.info("Validate we can start a scan, and receive one on finish.")

    rep = afspm_component.control_client.start_scan()

    # Ensure we get a scanning message
    scan_state_msg = scan_pb2.ScanStateMsg(
        scan_state=scan_pb2.ScanState.SS_SCANNING)
    assert rep == control_pb2.ControlResponse.REP_SUCCESS
    assert_sub_received_proto(afspm_component.subscriber,
                              scan_state_msg)

    # Ensure we received indication the scan ended, and an image
    scan_state_msg.scan_state = scan_pb2.ScanState.SS_FREE
    assert_sub_received_proto(afspm_component.subscriber,
                              scan_state_msg)
    assert afspm_component.subscriber.poll_and_store()
