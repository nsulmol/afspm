"""Test ScanHandler logic."""

import time
import pytest
import logging
import threading
import zmq

from afspm.io.protos.generated import scan_pb2
from afspm.io.protos.generated import control_pb2
from afspm.io.protos.generated import spec_pb2

from afspm.io.control.client import ControlClient
from afspm.io.control.server import ControlServer
from afspm.io.pubsub.publisher import Publisher
from afspm.io.pubsub.subscriber import Subscriber

from afspm.io import common

from afspm.components.scan.handler import ScanHandler
from afspm.components.microscope.actions import MicroscopeAction


logger = logging.getLogger(__name__)


# ----- Fixtures ----- #
# --- General / Urls --- #
@pytest.fixture
def ctx():
    return zmq.Context.instance()


@pytest.fixture(scope="module")
def server_url():
    return "tcp://127.0.0.1:9999"


@pytest.fixture(scope="module")
def publisher_url():
    return "tcp://127.0.0.1:9998"


@pytest.fixture()
def control_state():
    cs = control_pb2.ControlState()
    cs.control_mode = control_pb2.ControlMode.CM_AUTOMATED
    return cs


@pytest.fixture()
def scope_state_msg():
    ss = scan_pb2.ScopeStateMsg()
    ss.scope_state = scan_pb2.ScopeState.SS_FREE
    return ss


@pytest.fixture(scope="module")
def client_uuid():
    return "banana"


@pytest.fixture(scope="module")
def rerun_wait_s():
    return 1.0


# --- I/O Classes (Subscribers, Clients) --- #
@pytest.fixture
def server(server_url, ctx):
    return ControlServer(server_url, ctx, common.REQUEST_TIMEOUT_MS)


@pytest.fixture
def publisher(publisher_url, ctx):
    return Publisher(publisher_url, ctx=ctx)


@pytest.fixture
def handler_name():
    return 'MyScanHandler'


SCAN_PARAMS = common.create_scan_params_2d([0, 0], [200, 300],
                                           'nm')
PROBE_POS = common.create_probe_pos([1, 2], 'nm')


# --- Methods / thread routines --- #
def next_params_method_scan() -> scan_pb2.ScanParameters2d:
    return SCAN_PARAMS


def next_params_method_probe_pos() -> spec_pb2.ProbePosition:
    return PROBE_POS


def scan_handler_routine(publisher_url, rerun_wait_s,
                         server_url, client_uuid, ctx,
                         next_params_method, handler_name):
    logger.info("Startup scan_handler_routine")
    client = ControlClient(server_url, ctx, client_uuid)
    subscriber = Subscriber(publisher_url, ctx=ctx)
    scan_handler = ScanHandler(handler_name, rerun_wait_s, next_params_method)

    continue_running = True
    while continue_running:
        messages = subscriber.poll_and_store()
        if messages:
            for msg in messages:
                scan_handler.on_message_received(msg[1], client)
        elif subscriber.shutdown_was_requested:
            continue_running = False
    logger.info("Finishing scan_handler_routine")


@pytest.fixture
def thread_scan_handler(publisher_url, rerun_wait_s,
                        server_url, client_uuid, handler_name, ctx):
    thread = threading.Thread(target=scan_handler_routine,
                              args=(publisher_url, rerun_wait_s,
                                    server_url, client_uuid, ctx,
                                    next_params_method_scan, handler_name))
    thread.daemon = True
    thread.start()
    time.sleep(2*common.REQUEST_TIMEOUT_MS / 1000)
    return thread


@pytest.fixture
def thread_signal_handler(publisher_url, rerun_wait_s,
                          server_url, client_uuid, ctx):
    thread = threading.Thread(target=scan_handler_routine,
                              args=(publisher_url, rerun_wait_s,
                                    server_url, client_uuid, ctx,
                                    next_params_method_probe_pos))
    thread.daemon = True
    thread.start()
    time.sleep(2*common.REQUEST_TIMEOUT_MS / 1000)
    return thread


# ----- Tests ----- #
def test_experiment_problem(publisher, server, thread_scan_handler,
                            control_state, scope_state_msg):
    """Validate that we cannot run scans if the problem is improper."""
    logger.info("Validate that we cannot run scans if the problem is "
                "improper.")
    for problem in [control_pb2.ExperimentProblem.EP_TIP_SHAPE_CHANGED,
                    control_pb2.ExperimentProblem.EP_DEVICE_MALFUNCTION,
                    control_pb2.ExperimentProblem.EP_FEEDBACK_NON_OPTIMAL]:
        del control_state.problems_set[:]  # Clear problems set
        control_state.problems_set.append(problem)
        publisher.send_msg(control_state)
        publisher.send_msg(scope_state_msg)

        msg = server.poll()
        assert msg == (None, None)

    publisher.send_kill_signal()


def test_scanning(publisher, server, thread_scan_handler,
                  control_state, scope_state_msg):
    """Validate we can go through the scan process properly"""
    logger.info("Validate we can go through the scan process properly.")

    states = [scan_pb2.ScopeState.SS_MOVING,
              scan_pb2.ScopeState.SS_SCANNING]
    requests = [control_pb2.ControlRequest.REQ_SET_SCAN_PARAMS,
                control_pb2.ControlRequest.REQ_ACTION]
    objects = [SCAN_PARAMS,
               control_pb2.ActionMsg(action=MicroscopeAction.START_SCAN)]

    # Inform scan handler we are in the expected control state.
    publisher.send_msg(control_state)

    # Start up in SS_FREE
    scope_state_msg.scope_state = scan_pb2.ScopeState.SS_FREE
    publisher.send_msg(scope_state_msg)

    # Run 3 scans
    for i in list(range(3)):
        # Go through single scan process
        for state, exp_req, exp_obj in zip(states, requests, objects):
            req, obj = server.poll()
            assert req == exp_req
            assert obj == exp_obj
            server.reply(control_pb2.ControlResponse.REP_SUCCESS)

            scope_state_msg.scope_state = state
            publisher.send_msg(scope_state_msg)

            scope_state_msg.scope_state = scan_pb2.ScopeState.SS_FREE
            publisher.send_msg(scope_state_msg)

    logger.info("Sending kill signal")
    publisher.send_kill_signal()
    time.sleep(4*common.REQUEST_TIMEOUT_MS / 1000)


def test_spec(publisher, server, thread_signal_handler,
                      control_state, scope_state_msg):
    """Validate we can go through the spec process properly"""
    logger.info("Validate we can go through the spec process properly.")

    states = [scan_pb2.ScopeState.SS_MOVING,
              scan_pb2.ScopeState.SS_SPEC]
    requests = [control_pb2.ControlRequest.REQ_SET_PROBE_POS,
                control_pb2.ControlRequest.REQ_ACTION]
    objects = [PROBE_POS,
               control_pb2.ActionMsg(action=MicroscopeAction.START_SPEC)]

    # Inform scan handler we are in the expected control state.
    publisher.send_msg(control_state)

    # Start up in SS_FREE
    scope_state_msg.scope_state = scan_pb2.ScopeState.SS_FREE
    publisher.send_msg(scope_state_msg)

    # Run 3 scans
    for i in list(range(3)):
        # Go through single scan process
        for state, exp_req, exp_obj in zip(states, requests, objects):
            req, obj = server.poll()
            assert req == exp_req
            assert obj == exp_obj
            server.reply(control_pb2.ControlResponse.REP_SUCCESS)

            scope_state_msg.scope_state = state
            publisher.send_msg(scope_state_msg)

            scope_state_msg.scope_state = scan_pb2.ScopeState.SS_FREE
            publisher.send_msg(scope_state_msg)

    logger.info("Sending kill signal")
    publisher.send_kill_signal()
    time.sleep(4*common.REQUEST_TIMEOUT_MS / 1000)


def test_req_ctrl(publisher, server, thread_scan_handler, control_state,
                  scope_state_msg, rerun_wait_s):
    """Validate we try to gain control if we are not under control."""
    logger.info("Validate we try to gain control if we are not under control.")

    # Inform scan handler we are in the expected control state.
    publisher.send_msg(control_state)
    publisher.send_msg(scope_state_msg)

    req, __ = server.poll()
    assert req == control_pb2.ControlRequest.REQ_SET_SCAN_PARAMS
    server.reply(control_pb2.ControlResponse.REP_NOT_IN_CONTROL)

    # Confirm you received a request to gain control
    req, __ = server.poll()
    assert req == control_pb2.ControlRequest.REQ_REQUEST_CTRL

    publisher.send_kill_signal()
