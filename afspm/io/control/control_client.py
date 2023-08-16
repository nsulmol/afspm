"""Handles control requests to the AFSPM."""

import zmq
import logging

from . import commands as cmd

from ..protos.generated import control_pb2 as ctrl
from ..protos.generated import scan_pb2 as scan


logger = logging.getLogger(__name__)


class ControlClient:
    """Encapsulates logic for AFSPM requests.

    Handles:
    - requesting control of the device.
    - inserting or removing ExperimentProblems.
    - setting scan parameters and starting/stopping scans.

    Note: this follows the 'Lazy Pirate' pattern, as explained in Chapter 4
    of the zmq guide (called lpclient.py). It is almost exactly the same.

    Attributes:
        url: address of the server we are to connect to.
        ctx: zmq Context
        uuid: the socket's uuid string. By providing it, it allows any class
            using it to 'restart' properly after a crash. The reason this
            happens is simply: the next time we reconnect to a ROUTER, we have
            the same id as before. Thus, any 'state' is preserverd. If you *do
            not* provide a uuid and the crashed client was 'under control',
            this ControlClient will have in principle blocked the ControlRouter
            we are connected to!
        request_retries: how many times we will retry sending a message before
            giving up and returning a connection error.
        request_timeout_ms: how long we wait between request tries.
    """

    def __init__(self, url: str, ctx: zmq.Context = None,
                 uuid: str = None,
                 request_retries: int = 3,
                 request_timeout_ms: int = 2500):
        """Initialize, given server url and additional parms.

        Args:
            url: connection mechanism + address of the server we are to connect
                to.
            ctx: zmq Context to use to create our socket.
            uuid: a unique id string you want to use as this socket's
                identity. If None is provided, any ROUTER socket connected
                to it will create its own integer address.
            request_retries: how many times we will retry sending a message,
                before giving up and returning a connection error.
            request_timeout_ms: how long we wait between request tries.
        """
        if not ctx:
            ctx = zmq.Context.instance()

        self.url = url
        self.ctx = ctx
        self.uuid = uuid
        self.request_retries = request_retries
        self.request_timeout_ms = request_timeout_ms

        self.retries_left = request_retries

        self.client = None
        self._init_client()


    def _init_client(self):
        """Starts up (or restarts) the client socket."""
        if self.client and not self.client.closed:
            logger.error("client init, but exists and is not closed. Do nothing.")
            return
        self.client = self.ctx.socket(zmq.REQ)
        # Set identity (if provided)
        if self.uuid:
            self.client.setsockopt(zmq.IDENTITY, self.uuid.encode())
        self.client.connect(self.url)

    def _close_client(self):
        """Closes the client socket."""
        self.client.setsockopt(zmq.LINGER, 0)
        self.client.close

    def _try_send_req(self, msg: list[list[bytes]]
                      ) -> ctrl.ControlResponse:
        """Send provided message to server over client socket.

        Args:
            msg: list of bytes list (some messages may be multi-part). If your
                specific message is a single part, simply pass a 1-value list.

        Returns:
            RequestResponse enum indicating the response to our request.
        """
        retries_left = self.request_retries
        self.client.send_multipart(msg)

        while True:
            if (self.client.poll(self.request_timeout_ms) & zmq.POLLIN) != 0:
                # Response is expected to be int
                return cmd.parse_response(self.client.recv())
            retries_left -= 1
            logger.warning("No response from server")
            # Socket is confused. Close and remove it.
            self._close_client()

            if retries_left == 0:
                logger.error("Server seems to be offline, cannot send message")
                return ctrl.ControlResponse.REP_NO_RESPONSE

            logger.info("Reconnecting to server")
            self._init_client()
            self.client.send_multipart(msg)

    def start_scan(self) -> ctrl.ControlResponse:
        """Request start a scan.

        Returns:
            The received RequestResponse.
        """
        msg = cmd.serialize_req_obj(ctrl.ControlRequest.REQ_START_SCAN)
        return self._try_send_req(msg)

    def stop_scan(self) -> ctrl.ControlResponse:
        """Request stop a scan.

        Returns:
            The received RequestResponse.
        """
        msg = cmd.serialize_req_obj(ctrl.ControlRequest.REQ_STOP_SCAN)
        return self._try_send_req(msg)

    def set_scan_params(self, scan_params: scan.ScanParameters2d
                        ) -> ctrl.ControlResponse:
        """Try to set scan parameters for the SPM device.

        Args:
            scan_params: the desired scan parameters for the device.

        Returns:
            The received RequestResponse.
        """
        msg = cmd.serialize_req_obj(ctrl.ControlRequest.REQ_SET_SCAN_PARAMS,
                                    scan_params)
        return self._try_send_req(msg)

    def request_control(self, control_mode: ctrl.ControlMode
                        ) -> ctrl.ControlResponse:
        """Try to request control of the SPM device.

        To do so, we must indicate what ControlMode this client 'functions'
        under. On success, it indicates that (a) the SPM system is in the
        ControlMode of your request, and (b) the SPM system is not already
        under control.

        Args:
            control_mode: ControlMode enum indicating the control mode this
                client functions under.

        Returns:
            A RequestResponse enum indicating the success/failure of the
                request.
        """
        msg = cmd.serialize_req_obj(ctrl.ControlRequest.REQ_REQUEST_CTRL,
                                    control_mode)
        return self._try_send_req(msg)

    def release_control(self) -> ctrl.ControlResponse:
        """Request to release control from client.

        Returns:
            Response received from server.
        """
        msg = cmd.serialize_req_obj(ctrl.ControlRequest.REQ_RELEASE_CTRL)
        return self._try_send_req(msg)

    def add_experiment_problem(self, problem: ctrl.ExperimentProblem,
                               ) -> ctrl.ControlResponse:
        """Try to add an experiment problem to the SPM device.

        Args:
            problem: experiment problem to add.

        Return:
            Response received from server.
        """
        msg = cmd.serialize_req_obj(
            ctrl.ControlRequest.REQ_ADD_EXP_PRBLM, problem)
        return self._try_send_req(msg)

    def remove_experiment_problem(self, problem: ctrl.ExperimentProblem,
                                  ) -> ctrl.ControlResponse:
        """Try to remove an experiment problem to the SPM device.

        Args:
            problem: experiment problem to remove.

        Return:
            Response received from server.
        """
        msg = cmd.serialize_req_obj(ctrl.ControlRequest.REQ_RMV_EXP_PRBLM,
                                    problem)
        return self._try_send_req(msg)


class AdminControlClient(ControlClient):
    """Encapsulates logic for extra Administrator AFSPM requests.

    More specifically, this client is used to add a couple extra controls:
    - Setting the control mode.
    - Ending the experiment.

    The former should only really be done by the UI; the latter by the UI
    and/or the higher-level experiment class. We still allow this via the
    same control protocol for ease/development convenience. Put another way:
    we are allowing the user of this tool to break this tool; be caferul!
    """
    def set_control_mode(self, mode: ctrl.ControlMode) -> ctrl.ControlResponse:
        """Try to change the current control mode of the afspm system.

        Args:
            mode: desired ControlMode.

        Returns:
            Response received from the server.
        """
        msg = cmd.serialize_req_obj(ctrl.ControlRequests.REQ_SET_CONTROL_MODE,
                                    mode)
        return self._try_send_req(msg)

    def end_experiment(self) -> ctrl.ControlResponse:
       """Indicate the experiment should end.

       The AFSPM Controller should receive this request and notify all
       connected components to close.
       """
       msg = cmd.serialize_req_obj(ctrl.ControlRequests.REQ_END_EXPERIMENT)
       return self._try_send_req(msg)
