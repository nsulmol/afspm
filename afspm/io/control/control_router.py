"""Holds control router, for receiving requests from multiple different REQs."""

import copy
import zmq
import logging
from google.protobuf.message import Message

from . import commands as cmd

from ..protos.generated import control_pb2 as ctrl
from ..protos.generated import scan_pb2 as scan

logger = logging.getLogger(__name__)


class ControlRouter:
    """Encapsulates logic tied to requests from multipl ControlClients.

    The control router sits between a control server and multiple control
    clients, adding logic to decide between clients.

    Particularly:
    - Only one ControlClient can have control at a time. The logic for
        setting who is under control is within here (the client must
        request with the 'control_mode' it is currently in, and it must
        not currently be under control).
    - Any ControlClient can add or remove ExperimentProblems. If there
        are any problems in the internal list, the system cannot switch to
        ControlMode.CM_AUTOMATED.
    - All other commands are forwarded to the ControlServer *if* the client
        is under control.

    Attributes:
        backend: the REP socket that connects to the ControlServer.
        frontend: the ROUTER socket that connects with all ControlClients.
        problems_set: holds the set of problems which have been notified by
            ControlClients. As long as there are problems in this set, we
            cannot be in ControlMode.CM_AUTOMATED. However, 'automation'
            ControlClients that function in ControlMode.CM_PROBLEM will be
            allowed to take over and 'fix' a given problem.
        control_mode: what ControlMode we are currently running under. A
            ControlClient can only gain control if they request under
            the current control_mode (and no other client is currently
            under control).
        client_under_control: a uuid for the client currently under control.
        server_timeout_ms: delay to wait for a reply from the ControlServer.
    """

    def __init__(self, server_url: str, router_url: str,
                 ctx: zmq.Context = None,
                 server_timeout_ms: int = 1000):
        """Init the class.

        Args:
            server_url: the url of the ControlServer to connect to.
            router_url: the url of the Router, for ControlClients to connect
                to.
            ctx: zmq context.
            server_timeout_ms: delay to wait for a reply from the
                ControlServer.
        """
        if not ctx:
            ctx = zmq.Context.instance()

        self.backend = ctx.socket(zmq.REQ)
        self.backend.connect(server_url)

        self.frontend = ctx.socket(zmq.ROUTER)
        # Drop old sockets with same uuid
        self.frontend.setsockopt(zmq.ROUTER_HANDOVER, 1)
        self.frontend.bind(router_url)

        self.problems_set = set()

        self.control_mode = ctrl.ControlMode.CM_AUTOMATED
        self.client_under_control = None
        self.server_timeout_ms = server_timeout_ms

    def _handle_control_request(self, client: int,
                                control_mode: ctrl.ControlMode,
                                ) -> ctrl.ControlResponse:
        """Set client in control if possible.

        The client will only be placed under control if:
        - the provided control_mode matches the one this ControlRouter is
            currently under;
        - the ControlRouter is not currently under control.

        Args:
            client: uuid of client.
            control_mode: ControlMode of the client's request.

        Returns:
            - REP_SUCCESS if the above are met.
            - REP_ALREADY_UNDER_CONTROL if already under control.
            - REP_WRONG_CONTROL_MODE if there is a mismatch between
                control_mode of request and the one the ControlClient is
                currently under.
        """
        if self.client_under_control:
            return ctrl.ControlResponse.REP_ALREADY_UNDER_CONTROL
        if self.control_mode == control_mode:
            self.client_under_control = client
            return ctrl.ControlResponse.REP_SUCCESS
        return ctrl.ControlResponse.REP_WRONG_CONTROL_MODE

    def _handle_control_release(self, client: int) -> ctrl.ControlResponse:
        """Release client control if applicable.

        Control can only be released by the client who currently has control.

        Args:
            client: uuid of client.

        Returns:
            - REP_SUCCESS if the client was under control; we release.
            - REP_FAILUREif the client releasing was not under
                control to begin with (or no one was under control).
        """
        if self.client_under_control and self.client_under_control == client:
            self.client_under_control = None
            return ctrl.ControlResponse.REP_SUCCESS
        return ctrl.ControlResponse.REP_FAILURE

    def _handle_experiment_problem(self, add_problem: bool,
                                   exp_problem: ctrl.ExperimentProblem
                                   ) -> ctrl.ControlResponse:
        """Add or remove ExperimentProblems.

        Args:
            add_problem: if true, add the provided problem. if false, remove it.
            exp_problem: the ExperimentProblem to add/remove.

        Returns:
            ControlMode.SUCCESS if we were able to add it.
        """
        old_problems_set = copy.deepcopy(self.problems_set)
        if add_problem:
            self.problems_set.add(exp_problem)
        else:
            self.problems_set.remove(exp_problem)

        if not old_problems_set and self.problems_set:
            self.control_mode = ctrl.ControlMode.CM_PROBLEM
            self.client_under_control = None
        elif old_problems_set and not self.problems_set:
            self.control_mode = ctrl.ControlMode.CM_AUTOMATED
            self.client_under_control = None

        # Return success always for now...
        return ctrl.ControlResponse.REP_SUCCESS

    def _handle_send_req(self, req: ctrl.ControlRequest,
                         proto: Message) -> ctrl.ControlResponse:
        """Try to send a request to the ControlServer.

        For a request received from the client under control, try to forward
        it to the ControlServer.

        Note: we don't do extra handling for no response; we expect that
        to be handled by the client.

        Args:
            req: the DeviceControl request to be sent.
            proto: the associated protobuf message, if applicable.

        Returns:
            ControlResponse received from the ControlServer.
        """
        msg = cmd.serialize_req_obj(req, proto)  # No need for empty envelope
        self.backend.send_multipart(msg)

        if (self.backend.poll(self.server_timeout_ms) & zmq.POLLIN) != 0:
            return cmd.parse_response(self.backend.recv())
        return ctrl.ControlResponse.REP_NO_RESPONSE

    def _on_request(self, client: int, req: ctrl.ControlRequest,
                    obj: Message | int) -> ctrl.ControlResponse:
        """Handle a request received by a ControlClient.

        Args:
            client: ControlClient uuid.
            req: control request received.
            obj: protobuf message or int enum linked to request, if applicable.

        Returns:
            ControlResponse to the request.
        """

        if req == ctrl.ControlRequest.REQ_REQUEST_CTRL:
            return self._handle_control_request(client, obj)
        if req == ctrl.ControlRequest.REQ_RELEASE_CTRL:
            return self._handle_control_release(client)
        if req in [ctrl.ControlRequest.REQ_ADD_EXP_PRBLM,
                   ctrl.ControlRequest.REQ_RMV_EXP_PRBLM]:
            return self._handle_experiment_problem(
                req == ctrl.ControlRequest.REQ_ADD_EXP_PRBLM, obj)
        if (self.client_under_control
                and client == self.client_under_control):
            return self._handle_send_req(req, obj)
        return ctrl.ControlResponse.REP_NOT_IN_CONTROL

    def poll_and_handle(self, timeout_ms: int = 1000):
        """Poll for ControlClient requests and handle.

        Args:
            timeout_ms: the poll timeout, in milliseconds. If None,
                we do not poll and do a blocking receive instead.
        """
        msg = None
        if timeout_ms:
            if self.frontend.poll(timeout_ms, zmq.POLLIN):
                msg = self.frontend.recv_multipart(zmq.NOBLOCK)
        else:
            msg = self.frontend.recv_multipart()

        client = msg[0]
        if client:
            client_id = self._parse_client_id(client)
            req, obj = cmd.parse_request(msg[2:])  # client, __, ...
            rep = self._on_request(client_id, req, obj)
            self.frontend.send_multipart([client, b"",
                                          cmd.serialize_response(rep)])

    def set_control_mode(self, control_mode: ctrl.ControlMode):
        """Change the control mode.

        Args:
            control_mode: ControlMode to change to.
        """
        self.control_mode = control_mode
        self.client_under_control = None

    def remove_problem(self, exp_problem: ctrl.ExperimentProblem):
        """Remove an ExperimentProblem.

        Args:
            exp_problem ExperimentProblem to remove.
        """
        if exp_problem in self.problems_set:
            self.problems_set.remove(exp_problem)

    @staticmethod
    def _parse_client_id(msg: list[bytes]) -> str:
        """Parse the received client id to a string.

        The received client id will either be:
        - an int, if no zmq.IDENTITY was explicited for the socket;
        - a str, if a zmq.IDENTITY was explicited for the socket.

        Args:
            msg: client id received as a bytes array.

        Returns:
            string associated with the cliend id.
        """
        try:
            return msg.decode()  # zmq.IDENTITY used
        except UnicodeDecodeError:
            return int.from_bytes(msg, 'big')
