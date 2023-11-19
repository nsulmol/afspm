"""Test control request/server/router logic."""

from enum import Enum
from typing import Any
import threading
import pytest
import zmq

from afspm.io.control import commands as cmd
from afspm.io.control import client as ctrl_client
from afspm.io.control import server as ctrl_srvr
from afspm.io.control import router as ctrl_rtr

from afspm.io.protos.generated import scan_pb2
from afspm.io.protos.generated import control_pb2
from afspm.io.protos.generated import feedback_pb2


# ----- General Test Fixtures ----- #
@pytest.fixture(scope="module")
def ctx():
    return zmq.Context.instance()


@pytest.fixture(scope="module")
def server_url():
    return "tcp://127.0.0.1:7000"


@pytest.fixture(scope="module")
def router_url():
    return "tcp://127.0.0.1:7001"


@pytest.fixture(scope="module")
def comm_url():
    return "tcp://127.0.0.1:7002"


@pytest.fixture(scope="module")
def timeout_ms():
    return 100


@pytest.fixture(scope="module")
def kill_url():
    return "tcp://127.0.0.1:7003"


@pytest.fixture(scope="module")
def comm_pub(ctx, comm_url):
    comm_publisher = ctx.socket(zmq.PUB)
    comm_publisher.bind(comm_url)
    return comm_publisher


@pytest.fixture(scope="module")
def thread_srv(ctx, server_url, comm_url, timeout_ms):
    thread_server = threading.Thread(target=server_routine,
                                     args=(server_url, comm_url, timeout_ms, ctx))
    thread_server.daemon = True
    thread_server.start()
    return thread_server


@pytest.fixture
def thread_rtr(ctx, server_url, router_url, comm_url, timeout_ms):
    thread_router = threading.Thread(target=router_routine,
                                     args=(server_url, router_url, comm_url,
                                           timeout_ms, ctx))
    thread_router.daemon = True
    thread_router.start()
    return thread_router


@pytest.fixture
def problem():
    return control_pb2.ExperimentProblem.EP_TIP_SHAPE_CHANGED


# ----- Classes / Methods for communication with server/router threads ----- #
class CommEnvelope(str, Enum):
    ALL = ""
    KILL = "KILL"
    MODE = "MODE"


class CommMode(str, Enum):
    """Helper to control router."""
    MODE_AUTOMATED = "MODE_AUTOMATED"
    MODE_MANUAL = "MODE_MANUAL"
    MODE_PROBLEM = "MODE_PROBLEM"
    CLEAR_PROBLEMS = "CLEAR_PROBLEMS"


def get_comm_info(socket: zmq.Socket,
                  timeout_ms: int) -> (CommEnvelope, Any):
    """Get envelope and message from comm."""
    if socket.poll(timeout_ms, zmq.POLLIN):
        msg = socket.recv_multipart(zmq.NOBLOCK)
        envelope = CommEnvelope(msg[0].decode())
        rest = None
        if envelope == CommEnvelope.MODE:
            rest = CommMode(msg[1].decode())
        return envelope,rest
    return None, None


# ----- Server / Router Routines (for threading) ----- #
def server_routine(server_url, comm_url, timeout_ms, ctx):
    """Not killing, because server lifetime is full test module.

    When killing per test, we ran into errors where one server had
    not disconnected before the other had started. For now, simplifying
    by just leaving the server open for the whole test moudle.
    """
    comm = ctx.socket(zmq.SUB)
    comm.connect(comm_url)
    comm.setsockopt(zmq.SUBSCRIBE, b'')

    server = ctrl_srvr.ControlServer(server_url, ctx)

    while True:
        req, __ = server.poll()
        if req:
            rep = control_pb2.ControlResponse.REP_CMD_NOT_SUPPORTED
            if req in [control_pb2.ControlRequest.REQ_START_SCAN,
                       control_pb2.ControlRequest.REQ_STOP_SCAN,
                       control_pb2.ControlRequest.REQ_SET_SCAN_PARAMS,
                       control_pb2.ControlRequest.REQ_SET_ZCTRL_PARAMS,
                       control_pb2.ControlRequest.REQ_PARAM]:
                rep = control_pb2.ControlResponse.REP_SUCCESS

            obj = None
            if req == control_pb2.ControlRequest.REQ_PARAM:
                obj = control_pb2.ParameterMsg()
            server.reply(rep, obj)


def router_routine(server_url, router_url, comm_url, timeout_ms, ctx):
    # Set up listener for changing settings
    comm = ctx.socket(zmq.SUB)
    comm.connect(comm_url)
    comm.setsockopt(zmq.SUBSCRIBE, b'')

    router = ctrl_rtr.ControlRouter(server_url, router_url, ctx)

    while True:
        router.poll_and_handle()

        env, rest = get_comm_info(comm, timeout_ms)
        if env is not None:
            if env == CommEnvelope.MODE:
                mode = control_pb2.ControlMode.CM_AUTOMATED
                if rest == CommMode.MODE_MANUAL:
                    mode = control_pb2.ControlMode.CM_MANUAL
                elif rest == CommMode.MODE_PROBLEM:
                    mode = control_pb2.ControlMode.CM_PROBLEM
                router.set_control_mode(mode)
            elif env == CommEnvelope.KILL:
                break


# ----- Tests ----- #
class TestServerWithClient:
    """Server + Client tests."""
    @pytest.fixture
    def srv_client(self, server_url, ctx):
        return ctrl_client.ControlClient(server_url, ctx)
    @pytest.fixture
    def srv_client_server_methods(self, srv_client):
        return [(srv_client.start_scan, None),
                (srv_client.stop_scan, None),
                (srv_client.set_scan_params, scan_pb2.ScanParameters2d()),
                (srv_client.set_zctrl_params, feedback_pb2.ZCtrlParameters()),
                (srv_client.request_parameter, control_pb2.ParameterMsg())]
    @pytest.fixture
    def srv_client_router_methods(self, srv_client, problem):
        return [(srv_client.request_control, control_pb2.ControlMode.CM_AUTOMATED),
                (srv_client.add_experiment_problem, problem),
                (srv_client.remove_experiment_problem, problem)]

    def test_server_calls(self, ctx, server_url, comm_url, comm_pub,
                          timeout_ms, thread_srv, srv_client,
                          srv_client_server_methods):
        """Ensure server calls suceed."""
        for method, proto in srv_client_server_methods:
            rep = method(proto) if proto else method()

            # Handle special case of parameter set/get, where an obj is
            # returned.
            if isinstance(proto, control_pb2.ParameterMsg):
                assert rep[0] == control_pb2.ControlResponse.REP_SUCCESS
            else:
                assert rep == control_pb2.ControlResponse.REP_SUCCESS

    def test_router_calls(self, ctx, server_url, comm_url, comm_pub,
                          timeout_ms, thread_srv, srv_client,
                          srv_client_router_methods):
        """Ensure router calls fail (as there is no router!)."""
        for method, proto in srv_client_router_methods:
            rep = method(proto)
            assert rep == control_pb2.ControlResponse.REP_CMD_NOT_SUPPORTED


class TestRouterServerClient:
    """Server <-> Router <-> Clients tests."""
    @pytest.fixture
    def rtr_client(self, router_url, ctx):
        return ctrl_client.ControlClient(router_url, ctx)
    @pytest.fixture
    def rtr_client_server_methods(self, rtr_client):
        return [(rtr_client.start_scan, None),
                (rtr_client.stop_scan, None),
                (rtr_client.set_scan_params, scan_pb2.ScanParameters2d()),
                (rtr_client.set_zctrl_params, feedback_pb2.ZCtrlParameters())]
    @pytest.fixture
    def srv_client_router_methods(self, rtr_client, problem):
        return [(rtr_client.request_control, control_pb2.ControlMode.CM_AUTOMATED),
                (rtr_client.add_experiment_problem, problem),
                (rtr_client.remove_experiment_problem, problem)]

    def test_requests_without_control(self, ctx, server_url, router_url,
                                      comm_url, comm_pub, timeout_ms,
                                      thread_srv, thread_rtr,
                                      rtr_client, rtr_client_server_methods):
        """Server requests without gaining control should fail."""
        for method, proto in rtr_client_server_methods:
            rep = method(proto) if proto else method()
            assert rep == control_pb2.ControlResponse.REP_NOT_IN_CONTROL

        comm_pub.send_multipart([CommEnvelope.KILL.value.encode(),
                                 b''])


    def test_requests_with_control(self, ctx, server_url, router_url,
                                   comm_url, comm_pub, timeout_ms,
                                   thread_srv, thread_rtr,
                                   rtr_client, rtr_client_server_methods):
        """Server requests after gaining control should succeed."""
        rep = rtr_client.request_control(control_pb2.ControlMode.CM_AUTOMATED)
        assert rep == control_pb2.ControlResponse.REP_SUCCESS
        for method, proto in rtr_client_server_methods:
            rep = method(proto) if proto else method()
            assert rep == control_pb2.ControlResponse.REP_SUCCESS

        comm_pub.send_multipart([CommEnvelope.KILL.value.encode(),
                                 b''])

    def test_wrong_control_request(self, ctx, server_url, router_url,
                                   comm_url, comm_pub, timeout_ms,
                                   thread_srv, thread_rtr,
                                   rtr_client, rtr_client_server_methods):
        """Client requests wrong control mode."""
        rep = rtr_client.request_control(control_pb2.ControlMode.CM_MANUAL)
        assert rep == control_pb2.ControlResponse.REP_WRONG_CONTROL_MODE

        comm_pub.send_multipart([CommEnvelope.KILL.value.encode(),
                                 b''])


    def test_control_after_problem(self, ctx, server_url, router_url,
                                   comm_url, comm_pub, timeout_ms,
                                   thread_srv, thread_rtr, problem,
                                   rtr_client, rtr_client_server_methods):
        """An AUTOMATED client loses control after a PROBLEM is introduced.
        (But reconnecting with PROBLEM control works).
        """
        rep = rtr_client.request_control(control_pb2.ControlMode.CM_AUTOMATED)
        assert rep == control_pb2.ControlResponse.REP_SUCCESS

        rep = rtr_client.add_experiment_problem(problem)
        assert rep == control_pb2.ControlResponse.REP_SUCCESS

        for method, proto in rtr_client_server_methods:
            rep = method(proto) if proto else method()
            assert rep == control_pb2.ControlResponse.REP_NOT_IN_CONTROL

        rep = rtr_client.request_control(control_pb2.ControlMode.CM_PROBLEM)
        assert rep == control_pb2.ControlResponse.REP_SUCCESS

        for method, proto in rtr_client_server_methods:
            rep = method(proto) if proto else method()
            assert rep == control_pb2.ControlResponse.REP_SUCCESS

        comm_pub.send_multipart([CommEnvelope.KILL.value.encode(),
                                 b''])

    def test_swapping_control(self, ctx, server_url, router_url,
                              comm_url, comm_pub, timeout_ms,
                              thread_srv, thread_rtr, problem,
                              rtr_client, rtr_client_server_methods):
        """Two clients can swap control."""
        rep = rtr_client.request_control(control_pb2.ControlMode.CM_AUTOMATED)

        assert rep == control_pb2.ControlResponse.REP_SUCCESS

        new_client = ctrl_client.ControlClient(router_url, ctx)
        rep = new_client.request_control(control_pb2.ControlMode.CM_AUTOMATED)
        assert rep == control_pb2.ControlResponse.REP_ALREADY_UNDER_CONTROL

        rep = rtr_client.release_control()
        assert rep == control_pb2.ControlResponse.REP_SUCCESS

        new_client.request_control(control_pb2.ControlMode.CM_AUTOMATED)
        assert rep == control_pb2.ControlResponse.REP_SUCCESS

        rep = rtr_client.request_control(control_pb2.ControlMode.CM_AUTOMATED)
        assert rep == control_pb2.ControlResponse.REP_ALREADY_UNDER_CONTROL

        comm_pub.send_multipart([CommEnvelope.KILL.value.encode(),
                                 b''])


class TestCrashRestart:
    """Tests around crashing and restarting a client."""
    def test_no_uuid(self, ctx, router_url, timeout_ms, comm_pub,
                     thread_srv, thread_rtr):
        """With no UUID, a reconnection will brick the router (new uuid)."""
        client = ctrl_client.ControlClient(router_url, ctx)
        rep = client.request_control(control_pb2.ControlMode.CM_AUTOMATED)
        assert rep == control_pb2.ControlResponse.REP_SUCCESS
        assert client.start_scan() == control_pb2.ControlResponse.REP_SUCCESS

        del client

        client = ctrl_client.ControlClient(router_url, ctx)
        assert client.start_scan() == control_pb2.ControlResponse.REP_NOT_IN_CONTROL

        # Kill server and router
        comm_pub.send_multipart([CommEnvelope.KILL.value.encode(),
                                 b''])

    def test_with_uuid(self, ctx, router_url, timeout_ms, comm_pub,
                       thread_srv, thread_rtr):
        """With UUIDs provided, a reconnection should allow us to continue."""
        uuid = "banana"
        client = ctrl_client.ControlClient(router_url, ctx, uuid)
        rep = client.request_control(control_pb2.ControlMode.CM_AUTOMATED)
        assert rep == control_pb2.ControlResponse.REP_SUCCESS
        assert client.start_scan() == control_pb2.ControlResponse.REP_SUCCESS

        del client

        client = ctrl_client.ControlClient(router_url, ctx, uuid)
        assert client.start_scan() == control_pb2.ControlResponse.REP_SUCCESS

        # Kill server and router
        comm_pub.send_multipart([CommEnvelope.KILL.value.encode(),
                                 b''])
