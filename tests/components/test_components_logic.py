"""Test the general experiment flow logic (centered on MicroscopeTranslator)."""

import logging
import time
import copy
import threading
import pytest
import zmq

from google.protobuf.message import Message

from afspm.components.microscope.params import MicroscopeParameter
from afspm.components.component import AfspmComponentBase

from afspm.io import common
from afspm.io.pubsub.subscriber import Subscriber
from afspm.io.pubsub.logic import cache_logic as cl
from afspm.io.pubsub.logic import pbc_logic as pbc

from afspm.io.control.client import AdminControlClient

from afspm.io.protos.generated import scan_pb2
from afspm.io.protos.generated import control_pb2

from tests.components import sample_components as sc


logger = logging.getLogger(__name__)


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
def wait_count():
    return 3


@pytest.fixture(scope="module")
def wait_ms():
    return 2 * common.REQUEST_TIMEOUT_MS


# --- Cache Stuff --- #
@pytest.fixture(scope="module")
def cache_kwargs():
    return {"cache_logic": pbc.ProtoBasedCacheLogic()}


@pytest.fixture(scope="module")
def topics_scan2d():
    return [cl.CacheLogic.get_envelope_for_proto(scan_pb2.Scan2d())]


@pytest.fixture(scope="module")
def topics_states():
    return [cl.CacheLogic.get_envelope_for_proto(scan_pb2.ScopeStateMsg()),
            cl.CacheLogic.get_envelope_for_proto(control_pb2.ControlState())]


# --- I/O Classes (Subscribers, Clients) --- #
@pytest.fixture
def sub_scope_state(ctx, psc_url, topics_states, cache_kwargs, wait_ms):
    # Note: we use wait_ms because  we are explicitly checking per-call,
    # rather than looping.
    return Subscriber(
        psc_url, cl.extract_proto, topics_states,
        cl.update_cache, ctx,
        extract_proto_kwargs=cache_kwargs,
        update_cache_kwargs=cache_kwargs,
        poll_timeout_ms=wait_ms)


@pytest.fixture
def sub_scan2d(ctx, psc_url, topics_scan2d, cache_kwargs, wait_ms):
    # Note: we use wait_ms because  we are explicitly checking per-call,
    # rather than looping.
    return Subscriber(
        psc_url, cl.extract_proto, topics_scan2d,
        cl.update_cache, ctx,
        extract_proto_kwargs=cache_kwargs,
        update_cache_kwargs=cache_kwargs,
        poll_timeout_ms=wait_ms)


@pytest.fixture
def admin_client(router_url, ctx, component_name):
    return AdminControlClient(router_url, ctx, component_name)


@pytest.fixture
def no_problem():
    return control_pb2.ExperimentProblem.EP_NONE


# --- Main Test Classes --- #
# -- Microscope Translator Stuff -- #
@pytest.fixture(scope="module")
def scan_time_ms(wait_ms):
    return 5 * wait_ms


@pytest.fixture(scope="module")
def move_time_ms(wait_ms):
    return 2 * wait_ms


@pytest.fixture
def thread_microscope_translator(pub_url, server_url, psc_url, ctx, move_time_ms,
                                 scan_time_ms, cache_kwargs):
    thread = threading.Thread(target=sc.microscope_translator_routine,
                              args=(pub_url, server_url, psc_url,
                                    ctx, move_time_ms,
                                    scan_time_ms, cache_kwargs))
    thread.daemon = True
    thread.start()
    return thread


# -- Microscope Scheduler Stuff -- #
@pytest.fixture
def thread_microscope_scheduler(psc_url, pub_url, server_url, router_url,
                                cache_kwargs, ctx):
    thread = threading.Thread(target=sc.microscope_scheduler_routine,
                              args=(psc_url, pub_url, server_url, router_url,
                                    cache_kwargs, ctx))
    thread.daemon = True
    thread.start()
    return thread


# -- AfspmComponent Stuff -- #
@pytest.fixture
def component_name():
    return "TestComponent"


@pytest.fixture
def afspm_component(sub_scope_state, admin_client, component_name, ctx):
    return AfspmComponentBase(component_name, sub_scope_state, admin_client,
                              ctx)


# -------------------- Helper Methods -------------------- #
def assert_sub_received_proto(sub: Subscriber, proto: Message):
    """Confirm a message is received by a subscriber."""
    assert sub.poll_and_store()
    assert len(sub.cache[cl.CacheLogic.get_envelope_for_proto(proto)]) == 1
    assert (sub.cache[cl.CacheLogic.get_envelope_for_proto(proto)][0]
            == proto)


def startup_flush_messages(afspm_component: AfspmComponentBase,
                           wait_count: int):
    """On startup, we will receive a couple of messages. Flush them."""
    logger.debug("Starting flush messages")
    counter = 0
    while counter < wait_count:
        afspm_component.subscriber.poll_and_store()
        counter += 1
    logger.debug("Ending flush messages")


def request_control(afspm_component: AfspmComponentBase,
                    problem: control_pb2.ExperimentProblem,
                    default_control_state: control_pb2.ControlState,
                    component_name: str):
    """Request control with a component (and flush/validate messages)"""
    rep = afspm_component.control_client.request_control(problem)
    assert rep == control_pb2.ControlResponse.REP_SUCCESS

    cs = copy.deepcopy(default_control_state)
    cs.client_in_control_id = component_name

    assert_sub_received_proto(afspm_component.subscriber, cs)


def end_experiment(afspm_component: AfspmComponentBase):
    """End the experiment, so associated threads shut down."""
    rep = afspm_component.control_client.end_experiment()
    assert rep == control_pb2.ControlResponse.REP_SUCCESS
    afspm_component.subscriber.poll_and_store()
    assert afspm_component.subscriber.shutdown_was_requested


def wait_on_threads(thread_microscope_translator: threading.Thread,
                    thread_microscope_scheduler: threading.Thread):
    thread_microscope_scheduler.join()
    thread_microscope_translator.join()


def startup_and_req_ctrl(afspm_component: AfspmComponentBase,
                         problem: control_pb2.ExperimentProblem,
                         default_control_state: control_pb2.ControlState,
                         component_name: str, wait_count: int):
    """Calls the above 2 one after the other."""
    startup_flush_messages(afspm_component, wait_count)
    request_control(afspm_component, problem, default_control_state,
                    component_name)


def end_and_wait_threads(afspm_component: AfspmComponentBase,
                         thread_microscope_translator: threading.Thread,
                         thread_microscope_scheduler: threading.Thread):
    end_experiment(afspm_component)
    wait_on_threads(thread_microscope_translator, thread_microscope_scheduler)


# -------------------- Tests -------------------- #
def test_end_experiment(thread_microscope_translator,
                        thread_microscope_scheduler,
                        afspm_component, wait_count, ctx):
    """Ensure we can end the experiment."""
    startup_flush_messages(afspm_component, wait_count)
    end_and_wait_threads(afspm_component, thread_microscope_translator,
                         thread_microscope_scheduler)


def test_get_release_control(thread_microscope_translator,
                             thread_microscope_scheduler, no_problem,
                             afspm_component, wait_count, move_time_ms,
                             component_name, default_control_state, ctx):
    """Ensure we can obtain and release control."""
    startup_and_req_ctrl(afspm_component, no_problem, default_control_state,
                         component_name, wait_count)

    rep = afspm_component.control_client.release_control()
    assert rep == control_pb2.ControlResponse.REP_SUCCESS
    assert_sub_received_proto(afspm_component.subscriber,
                              default_control_state)

    end_and_wait_threads(afspm_component, thread_microscope_translator,
                         thread_microscope_scheduler)


def test_start_scan(thread_microscope_translator, thread_microscope_scheduler,
                    afspm_component, wait_count, scan_time_ms, no_problem,
                    sub_scan2d, component_name, default_control_state):
    """Ensure we receive indication of a scan starting when we request it."""
    startup_and_req_ctrl(afspm_component, no_problem, default_control_state,
                         component_name, wait_count)

    rep = afspm_component.control_client.start_scan()
    assert rep == control_pb2.ControlResponse.REP_SUCCESS

    scope_state_msg = scan_pb2.ScopeStateMsg(
        scope_state=scan_pb2.ScopeState.SS_COLLECTING)
    assert_sub_received_proto(afspm_component.subscriber,
                              scope_state_msg)
    assert not afspm_component.subscriber.poll_and_store()

    # Wait for scan to finish
    time.sleep(2 * scan_time_ms / 1000)

    # Ensure we received indication the scan ended, and an image
    scope_state_msg.scope_state = scan_pb2.ScopeState.SS_FREE
    assert_sub_received_proto(afspm_component.subscriber,
                              scope_state_msg)
    # Validate we received a new image.
    assert sub_scan2d.poll_and_store()

    end_and_wait_threads(afspm_component, thread_microscope_translator,
                         thread_microscope_scheduler)


def test_stop_scan(thread_microscope_translator, thread_microscope_scheduler,
                   afspm_component, wait_count, scan_time_ms, no_problem,
                   sub_scan2d, default_control_state, component_name):
    """Ensure that we can cancel a scan and receive updates."""
    startup_and_req_ctrl(afspm_component, no_problem, default_control_state,
                         component_name, wait_count)

    rep = afspm_component.control_client.start_scan()
    scope_state_msg = scan_pb2.ScopeStateMsg(
        scope_state=scan_pb2.ScopeState.SS_COLLECTING)

    assert rep == control_pb2.ControlResponse.REP_SUCCESS
    assert_sub_received_proto(afspm_component.subscriber,
                              scope_state_msg)
    # No more messages until scan done
    assert not afspm_component.subscriber.poll_and_store()
    assert not sub_scan2d.poll_and_store()

    # Cancel scan before it finishes!
    afspm_component.control_client.stop_scan()

    # First, will receive an SS_INTERRUPTED state; then, an SS_FREE state.
    for state in [scan_pb2.ScopeState.SS_INTERRUPTED,
                  scan_pb2.ScopeState.SS_FREE]:
        scope_state_msg.scope_state = state
        assert_sub_received_proto(afspm_component.subscriber,
                                  scope_state_msg)

    assert not sub_scan2d.poll_and_store()
    assert not afspm_component.subscriber.poll_and_store()

    end_and_wait_threads(afspm_component, thread_microscope_translator,
                         thread_microscope_scheduler)


def test_set_scan_params(thread_microscope_translator,
                         thread_microscope_scheduler, no_problem,
                         afspm_component, wait_count, move_time_ms,
                         sub_scan2d, default_control_state, component_name):
    """Ensure that we receive motion messages when we change scan params.

    Here, we are explicitly linking a scan params call to SS_MOVING. With a
    real SPM, it would depend on whether the spatial roi has changed.
    """
    startup_and_req_ctrl(afspm_component, no_problem, default_control_state,
                         component_name, wait_count)

    rep = afspm_component.control_client.set_scan_params(
        scan_pb2.ScanParameters2d())
    scope_state_msg = scan_pb2.ScopeStateMsg(
        scope_state=scan_pb2.ScopeState.SS_MOVING)

    assert rep == control_pb2.ControlResponse.REP_SUCCESS
    assert_sub_received_proto(afspm_component.subscriber,
                              scope_state_msg)
    assert not sub_scan2d.poll_and_store()

    # Wait for move to finish
    time.sleep(2 * move_time_ms / 1000)

    scope_state_msg.scope_state = scan_pb2.ScopeState.SS_FREE
    assert_sub_received_proto(afspm_component.subscriber,
                              scope_state_msg)
    assert not sub_scan2d.poll_and_store()
    assert not afspm_component.subscriber.poll_and_store()

    end_and_wait_threads(afspm_component, thread_microscope_translator,
                         thread_microscope_scheduler)


def test_experiment_problems(thread_microscope_translator, thread_microscope_scheduler,
                             afspm_component, wait_count, move_time_ms,
                             default_control_state):
    """Ensure we can set/unset experiment problems and receive in sub."""
    startup_flush_messages(afspm_component, wait_count)

    problem = control_pb2.ExperimentProblem.EP_TIP_SHAPE_CHANGED
    rep = afspm_component.control_client.add_experiment_problem(problem)
    assert rep == control_pb2.ControlResponse.REP_SUCCESS

    cs = copy.deepcopy(default_control_state)
    cs.control_mode = control_pb2.ControlMode.CM_PROBLEM
    cs.problems_set.append(problem)
    assert_sub_received_proto(afspm_component.subscriber, cs)

    rep = afspm_component.control_client.remove_experiment_problem(problem)
    assert rep == control_pb2.ControlResponse.REP_SUCCESS
    assert_sub_received_proto(afspm_component.subscriber,
                              default_control_state)

    end_and_wait_threads(afspm_component, thread_microscope_translator,
                         thread_microscope_scheduler)


def test_calls_while_scanning(thread_microscope_translator, no_problem,
                              thread_microscope_scheduler, wait_count,
                              afspm_component, default_control_state,
                              component_name, scan_time_ms):
    """Confirm that very few calls can be run while scanning."""
    startup_and_req_ctrl(afspm_component, no_problem, default_control_state,
                         component_name, wait_count)

    rep = afspm_component.control_client.start_scan()
    scope_state_msg = scan_pb2.ScopeStateMsg(
        scope_state=scan_pb2.ScopeState.SS_COLLECTING)
    assert rep == control_pb2.ControlResponse.REP_SUCCESS
    assert_sub_received_proto(afspm_component.subscriber,
                              scope_state_msg)

    # Note: update this if we add more MicroscopeTranslator commands!
    unallowed_commands_for_scan = {
        afspm_component.control_client.set_scan_params:
        scan_pb2.ScanParameters2d()}

    for command in unallowed_commands_for_scan:
        arg = unallowed_commands_for_scan[command]
        rep = command(arg) if arg is not None else command()
        assert rep == control_pb2.ControlResponse.REP_NOT_FREE

    # Wait for scan to finish
    time.sleep(2 * scan_time_ms / 1000)
    assert afspm_component.subscriber.poll_and_store()

    end_and_wait_threads(afspm_component, thread_microscope_translator,
                         thread_microscope_scheduler)


def test_set_control_mode(thread_microscope_translator, thread_microscope_scheduler,
                          afspm_component, wait_count, default_control_state):
    """Confirm we can set the control mode."""
    startup_flush_messages(afspm_component, wait_count)

    mode = control_pb2.ControlMode.CM_MANUAL
    rep = afspm_component.control_client.set_control_mode(mode)
    assert rep == control_pb2.ControlResponse.REP_SUCCESS

    cs = copy.deepcopy(default_control_state)
    cs.control_mode = mode
    assert_sub_received_proto(afspm_component.subscriber, cs)

    end_and_wait_threads(afspm_component, thread_microscope_translator,
                         thread_microscope_scheduler)


def test_set_get_params(thread_microscope_translator,
                        thread_microscope_scheduler, no_problem,
                        afspm_component, wait_count, default_control_state,
                        component_name):
    """Confirm we can get/set supported params, and fail on unsupported."""
    startup_and_req_ctrl(afspm_component, no_problem, default_control_state,
                         component_name, wait_count)

    # Non-existent param returns invalid param message
    param_msg = control_pb2.ParameterMsg(parameter='ougadougou')
    rep, obj = afspm_component.control_client.request_parameter(param_msg)
    assert rep == control_pb2.ControlResponse.REP_PARAM_INVALID

    # Unsupported param returns unsupported message.
    param_msg = control_pb2.ParameterMsg(
        parameter=MicroscopeParameter.TIP_BIAS_VOLTAGE)
    logger.warning(f'param_msg: {param_msg}')
    rep, obj = afspm_component.control_client.request_parameter(param_msg)
    assert rep == control_pb2.ControlResponse.REP_PARAM_NOT_SUPPORTED

    # Supported param succeeds.
    param_msg = control_pb2.ParameterMsg(
        parameter=MicroscopeParameter.SCAN_SPEED)
    rep, param_msg = afspm_component.control_client.request_parameter(param_msg)
    assert rep == control_pb2.ControlResponse.REP_SUCCESS

    # val from sample_components.py::SampleMicroscopeTranslator
    assert param_msg.value == str(500)

    new_val = str(750.0)
    param_msg.value = new_val
    rep, param_msg = afspm_component.control_client.request_parameter(param_msg)
    assert rep == control_pb2.ControlResponse.REP_SUCCESS
    assert param_msg.value == new_val

    # Setting failure is passed onto client. This one gets properly, but
    # fails on a set.
    param_msg = control_pb2.ParameterMsg(
        parameter=MicroscopeParameter.MOVING_SPEED)
    rep, param_msg = afspm_component.control_client.request_parameter(param_msg)
    assert rep == control_pb2.ControlResponse.REP_SUCCESS

    param_msg.value = "33"
    rep, param_msg = afspm_component.control_client.request_parameter(param_msg)
    assert rep == control_pb2.ControlResponse.REP_PARAM_ERROR
