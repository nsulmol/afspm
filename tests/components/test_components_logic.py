"""Test the general experiment flow logic (centered on AfspmController)."""

import time
import copy
import threading
import pytest
import zmq

from google.protobuf.message import Message
from google.protobuf.timestamp_pb2 import Timestamp

from afspm.components.device_controller import DeviceController
from afspm.components.afspm_controller import AfspmController
from afspm.components.afspm_component import AfspmComponent


from afspm.io.pubsub.subscriber import Subscriber
from afspm.io.pubsub.publisher import Publisher
from afspm.io.pubsub.pubsubcache import PubSubCache
from afspm.io.cache import cache_logic as cl
from afspm.io.cache import pbc_logic as pbc


from afspm.io.control.control_server import ControlServer
from afspm.io.control.control_router import ControlRouter
from afspm.io.control.control_client import ControlClient, AdminControlClient

from afspm.io.protos.generated import scan_pb2
from afspm.io.protos.generated import control_pb2

from tests.components import sample_components as sc


# -------------------- Fixtures -------------------- #
# --- General / Urls --- #
@pytest.fixture
def ctx():
    return zmq.Context.instance()


@pytest.fixture(scope="module")
def pub_url():
    return "tcp://127.0.0.1:9000"


@pytest.fixture(scope="module")
def psc_url():
    return "tcp://127.0.0.1:9001"


@pytest.fixture(scope="module")
def server_url():
    return "tcp://127.0.0.1:6666"


@pytest.fixture(scope="module")
def router_url():
    return "tcp://127.0.0.1:6667"


@pytest.fixture(scope="module")
def default_control_state():
    cs = control_pb2.ControlState()
    cs.control_mode = control_pb2.ControlMode.CM_AUTOMATED
    return cs


# --- Timing Stuff --- #
@pytest.fixture(scope="module")
def wait_ms():
    return 100


@pytest.fixture(scope="module")
def poll_timeout_ms():
    return 25

@pytest.fixture(scope="module")
def loop_sleep_s():
    return 0


@pytest.fixture(scope="module")
def hb_period_s():
    return 0.1


# --- Cache Stuff --- #
@pytest.fixture(scope="module")
def cache_kwargs():
    return {"cache_logic": pbc.ProtoBasedCacheLogic()}


@pytest.fixture(scope="module")
def topics_scan2d():
    return [cl.CacheLogic.get_envelope_for_proto(scan_pb2.Scan2d())]


@pytest.fixture(scope="module")
def topics_states():
    return [cl.CacheLogic.get_envelope_for_proto(
        scan_pb2.ScanStateMsg()),
            cl.CacheLogic.get_envelope_for_proto(
                control_pb2.ControlState())
            ]


# --- I/O Classes (Subscribers, Clients) --- #
@pytest.fixture
def sub_scan_state(ctx, psc_url, topics_states, cache_kwargs,
        wait_ms):
    return Subscriber(
        psc_url, cl.extract_proto, topics_states,
        cl.update_cache, ctx,
        extract_proto_kwargs=cache_kwargs,
        update_cache_kwargs=cache_kwargs)


@pytest.fixture
def sub_scan2d(ctx, psc_url, topics_scan2d, cache_kwargs,
               wait_ms):
    return Subscriber(
        psc_url, cl.extract_proto, topics_scan2d,
        cl.update_cache, ctx,
        extract_proto_kwargs=cache_kwargs,
        update_cache_kwargs=cache_kwargs)


# Testing with wait_ms instead of poll_tiomeout_ms for client!!!
# Effectively, the client *NEEDS* to wait a reasonable amount
# longer than the server or router. Otherwise, they will miss messages!
@pytest.fixture
def admin_client(router_url, ctx, component_name, wait_ms):
    return AdminControlClient(router_url, ctx, component_name,
                              request_timeout_ms=wait_ms)


# --- Main Test Classes --- #
# -- Device Controller Stuff -- #
@pytest.fixture#(scope="module")
def scan_time_ms():
    return 1500


@pytest.fixture#(scope="module")
def move_time_ms():
    return 250


@pytest.fixture
def thread_device_controller(pub_url, server_url, psc_url, poll_timeout_ms,
                             loop_sleep_s, hb_period_s, ctx, move_time_ms,
                             scan_time_ms, cache_kwargs):
    thread = threading.Thread(target=sc.device_controller_routine,
                              args=(pub_url, server_url, psc_url,
                                    poll_timeout_ms, loop_sleep_s,
                                    hb_period_s, ctx, move_time_ms,
                                    scan_time_ms, cache_kwargs))
    thread.daemon = True
    thread.start()
    return thread


# -- Afspm Controller Stuff -- #
@pytest.fixture
def thread_afspm_controller(psc_url, pub_url, server_url, router_url,
                            cache_kwargs, loop_sleep_s, hb_period_s,
                            poll_timeout_ms, ctx):
    thread = threading.Thread(target=sc.afspm_controller_routine,
                              args=(psc_url, pub_url, server_url, router_url,
                                    cache_kwargs, loop_sleep_s, hb_period_s,
                                    poll_timeout_ms, ctx))
    thread.daemon = True
    thread.start()
    return thread


# -- AfspmComponent Stuff -- #
@pytest.fixture
def component_name():
    return "TestComponent"


@pytest.fixture
def afspm_component(loop_sleep_s, hb_period_s, sub_scan_state, admin_client,
                    component_name, poll_timeout_ms, ctx):
    return AfspmComponent(component_name, loop_sleep_s, hb_period_s,
                          poll_timeout_ms, sub_scan_state, admin_client,
                          ctx)


# -------------------- Helper Methods -------------------- #
def assert_sub_received_proto(sub: Subscriber,
                              proto: Message,
                              wait_ms: int):
    """Confirm a message is received by a subscriber."""
    assert sub.poll_and_store(wait_ms)
    assert len(sub.cache[cl.CacheLogic.get_envelope_for_proto(proto)]) == 1
    assert (sub.cache[cl.CacheLogic.get_envelope_for_proto(proto)][0]
            == proto)


def startup_flush_messages(afspm_component: AfspmController,
                           wait_ms: int):
    """On startup, we will receive a couple of messages. Flush them."""
    received = True
    while received:
        received = afspm_component.subscriber.poll_and_store(wait_ms)


def request_control(afspm_component: AfspmController, wait_ms: int,
                    default_control_state: control_pb2.ControlState,
                    component_name: str):
    """Request control with a component (and flush/validate messages)"""
    rep = afspm_component.control_client.request_control(
        control_pb2.ControlMode.CM_AUTOMATED)
    assert rep == control_pb2.ControlResponse.REP_SUCCESS

    cs = copy.deepcopy(default_control_state)
    cs.client_in_control_id = component_name

    assert_sub_received_proto(afspm_component.subscriber,
                              cs, wait_ms)


def end_experiment(afspm_component: AfspmComponent,
                   wait_ms: int):
    """End the experiment, so associated threads shut down."""
    rep = afspm_component.control_client.end_experiment()
    assert rep == control_pb2.ControlResponse.REP_SUCCESS
    afspm_component.subscriber.poll_and_store(wait_ms)
    assert afspm_component.subscriber.was_shutdown_requested()

    # TODO: Look into removing this. Ugly delay to get stuff to
    # close properly in pytest.
    time.sleep(4*wait_ms / 1000)



def wait_on_threads(thread_device_controller: threading.Thread,
                    thread_afspm_controller: threading.Thread):
    thread_afspm_controller.join()
    thread_device_controller.join()


def startup_and_req_ctrl(afspm_component: AfspmController, wait_ms: int,
                 default_control_state: control_pb2.ControlState,
                 component_name: str):
    """Calls the above 2 one after the other."""
    startup_flush_messages(afspm_component, wait_ms)
    request_control(afspm_component, wait_ms, default_control_state,
                    component_name)


def end_and_wait_threads(afspm_component: AfspmComponent,
                         wait_ms: int,
                         thread_device_controller: threading.Thread,
                         thread_afspm_controller: threading.Thread):
    end_experiment(afspm_component, wait_ms)
    wait_on_threads(thread_device_controller, thread_afspm_controller)


# -------------------- Tests -------------------- #
def test_end_experiment(thread_device_controller, thread_afspm_controller,
                        afspm_component, wait_ms, ctx):
    """Ensure we can end the experiment."""
    startup_flush_messages(afspm_component, wait_ms)
    end_and_wait_threads(afspm_component, wait_ms, thread_device_controller,
                         thread_afspm_controller)


def test_get_release_control(thread_device_controller, thread_afspm_controller,
                             afspm_component, wait_ms, move_time_ms,
                             component_name, default_control_state, ctx):
    """Ensure we can obtain and release control."""
    startup_and_req_ctrl(afspm_component, wait_ms, default_control_state,
                         component_name)

    rep = afspm_component.control_client.release_control()
    assert rep == control_pb2.ControlResponse.REP_SUCCESS
    assert_sub_received_proto(afspm_component.subscriber,
                              default_control_state, wait_ms)

    end_and_wait_threads(afspm_component, wait_ms, thread_device_controller,
                         thread_afspm_controller)


def test_start_scan(thread_device_controller, thread_afspm_controller,
                    afspm_component, wait_ms, scan_time_ms,
                    sub_scan2d, component_name, default_control_state):
    """Ensure that we receive indication of a scan starting when we request it."""
    startup_and_req_ctrl(afspm_component, wait_ms, default_control_state,
                         component_name)

    rep = afspm_component.control_client.start_scan()
    scan_state_msg = scan_pb2.ScanStateMsg(
        scan_state=scan_pb2.ScanState.SS_SCANNING)
    assert rep == control_pb2.ControlResponse.REP_SUCCESS

    assert_sub_received_proto(afspm_component.subscriber,
                              scan_state_msg,
                              wait_ms)
    assert not afspm_component.subscriber.poll_and_store(wait_ms)

    # Wait for scan to finish
    time.sleep(2 * scan_time_ms / 1000)

    # Ensure we received indication the scan ended, and an image
    scan_state_msg.scan_state = scan_pb2.ScanState.SS_FREE
    assert_sub_received_proto(afspm_component.subscriber,
                              scan_state_msg,
                              wait_ms)
    # Validate we received a new image.
    assert sub_scan2d.poll_and_store(wait_ms)

    end_and_wait_threads(afspm_component, wait_ms, thread_device_controller,
                         thread_afspm_controller)


# TODO: update, this will fail! Need to add a check for scan changing to interrupted
def test_stop_scan(thread_device_controller, thread_afspm_controller,
                   afspm_component, wait_ms, scan_time_ms,
                   sub_scan2d, default_control_state, component_name):
    """Ensure that we can cancel a scan and receive updates."""
    startup_and_req_ctrl(afspm_component, wait_ms, default_control_state,
                         component_name)

    rep = afspm_component.control_client.start_scan()
    scan_state_msg = scan_pb2.ScanStateMsg(
        scan_state=scan_pb2.ScanState.SS_SCANNING)

    assert rep == control_pb2.ControlResponse.REP_SUCCESS
    assert_sub_received_proto(afspm_component.subscriber,
                              scan_state_msg,
                              wait_ms)
    # No more messages until scan done
    assert not afspm_component.subscriber.poll_and_store(wait_ms)
    assert not sub_scan2d.poll_and_store(wait_ms)

    # Cancel scan before it finishes!
    afspm_component.control_client.stop_scan()

    # First, will receive an SS_INTERRUPTED state; then, an SS_FREE state.
    for state in [scan_pb2.ScanState.SS_INTERRUPTED,
                  scan_pb2.ScanState.SS_FREE]:
        scan_state_msg.scan_state = state
        assert_sub_received_proto(afspm_component.subscriber,
                                  scan_state_msg,
                                  wait_ms)

    assert not sub_scan2d.poll_and_store(wait_ms)
    assert not afspm_component.subscriber.poll_and_store(wait_ms)

    end_and_wait_threads(afspm_component, wait_ms, thread_device_controller,
                         thread_afspm_controller)


def test_set_scan_params(thread_device_controller, thread_afspm_controller,
                         afspm_component, wait_ms, move_time_ms,
                         sub_scan2d, default_control_state, component_name):
    """Ensure that we receive motion messages when we change scan params.

    Here, we are explicitly linking a scan params call to SS_MOVING. With a
    real SPM, it would depend on whether the spatial roi has changed.
    """
    startup_and_req_ctrl(afspm_component, wait_ms, default_control_state,
                         component_name)

    rep = afspm_component.control_client.set_scan_params(
        scan_pb2.ScanParameters2d())
    scan_state_msg = scan_pb2.ScanStateMsg(
        scan_state=scan_pb2.ScanState.SS_MOVING)

    assert rep == control_pb2.ControlResponse.REP_SUCCESS
    assert_sub_received_proto(afspm_component.subscriber,
                              scan_state_msg,
                              wait_ms)
    assert not sub_scan2d.poll_and_store(wait_ms)

    # Wait for move to finish
    time.sleep(2 * move_time_ms / 1000)

    scan_state_msg.scan_state = scan_pb2.ScanState.SS_FREE
    assert_sub_received_proto(afspm_component.subscriber,
                              scan_state_msg,
                              wait_ms)
    assert not sub_scan2d.poll_and_store(wait_ms)
    assert not afspm_component.subscriber.poll_and_store(wait_ms)

    end_and_wait_threads(afspm_component, wait_ms, thread_device_controller,
                         thread_afspm_controller)


def test_experiment_problems(thread_device_controller, thread_afspm_controller,
                             afspm_component, wait_ms, move_time_ms,
                             default_control_state):
    """Ensure we can set/unset experiment problems and receive in sub."""
    startup_flush_messages(afspm_component, wait_ms)

    problem = control_pb2.ExperimentProblem.EP_TIP_SHAPE_CHANGED
    rep = afspm_component.control_client.add_experiment_problem(problem)
    assert rep == control_pb2.ControlResponse.REP_SUCCESS

    cs = copy.deepcopy(default_control_state)
    cs.control_mode = control_pb2.ControlMode.CM_PROBLEM
    cs.problems_set.append(problem)
    assert_sub_received_proto(afspm_component.subscriber,
                              cs, wait_ms)

    rep = afspm_component.control_client.remove_experiment_problem(problem)
    assert rep == control_pb2.ControlResponse.REP_SUCCESS
    assert_sub_received_proto(afspm_component.subscriber,
                              default_control_state, wait_ms)

    end_and_wait_threads(afspm_component, wait_ms, thread_device_controller,
                         thread_afspm_controller)


def test_calls_while_scanning(thread_device_controller,
                              thread_afspm_controller,
                              afspm_component, wait_ms, default_control_state,
                              component_name, scan_time_ms):
    """Confirm that very few calls can be run while scanning."""
    startup_and_req_ctrl(afspm_component, wait_ms, default_control_state,
                         component_name)

    rep = afspm_component.control_client.start_scan()
    scan_state_msg = scan_pb2.ScanStateMsg(
        scan_state=scan_pb2.ScanState.SS_SCANNING)
    assert rep == control_pb2.ControlResponse.REP_SUCCESS
    assert_sub_received_proto(afspm_component.subscriber,
                              scan_state_msg,
                              wait_ms)

    # Note: update this if we add more DeviceController commands!
    unallowed_commands_for_scan = {
        afspm_component.control_client.set_scan_params:
        scan_pb2.ScanParameters2d()}

    for command in unallowed_commands_for_scan:
        arg = unallowed_commands_for_scan[command]
        rep = command(arg) if arg is not None else command()
        assert rep == control_pb2.ControlResponse.REP_NOT_FREE

    # TODO: Remove this (waiting for the scan to finish) and get running
    # again
    # Wait for scan to finish
    time.sleep(2 * scan_time_ms / 1000)
    assert afspm_component.subscriber.poll_and_store(wait_ms)

    end_and_wait_threads(afspm_component, wait_ms, thread_device_controller,
                         thread_afspm_controller)


def test_set_control_mode(thread_device_controller, thread_afspm_controller,
                          afspm_component, wait_ms, default_control_state):
    """Confirm we can set the control mode."""
    startup_flush_messages(afspm_component, wait_ms)

    mode = control_pb2.ControlMode.CM_MANUAL
    rep = afspm_component.control_client.set_control_mode(mode)
    assert rep == control_pb2.ControlResponse.REP_SUCCESS

    cs = copy.deepcopy(default_control_state)
    cs.control_mode = mode
    assert_sub_received_proto(afspm_component.subscriber,
                              cs, wait_ms)

    end_and_wait_threads(afspm_component, wait_ms, thread_device_controller,
                         thread_afspm_controller)
