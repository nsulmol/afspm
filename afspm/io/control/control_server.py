"""Holds control server, for encapsulating some communication logic."""

import zmq
import logging

from google.protobuf.message import Message

from . import commands as cmd
from .. import common
from ..protos.generated import control_pb2 as ctrl

logger = logging.getLogger(__name__)


class ControlServer:
    """Encapsulates logic for responding to DeviceControl requests.

    The expected functionality is:
    - Within your main loop, call recv() regularly to check for any
    incoming requests.
    - If one was received, handle it appropriately and call reply()
    as soon as possible.

    Attributes:
        server: the REP socket associated with our server
        poll_timeout_ms: how long to wait when polling for messages.
            If None, we do not poll and do a blocking receive instead.
    """

    def __init__(self, url: str, ctx: zmq.Context = None,
                 poll_timeout_ms: int = common.POLL_TIMEOUT_MS,
                 **kwargs):
        self.poll_timeout_ms = poll_timeout_ms
        if not ctx:
            ctx = zmq.Context.instance()

        self.server = ctx.socket(zmq.REP)
        self.server.bind(url)

        common.sleep_on_socket_startup()

    def poll(self) -> (ctrl.ControlRequest, Message):
        """Poll for message and return if received.

        We use a poll() first, to ensure there is a message to receive.
        If self.poll_timeout_ms is None, we do a blocking receive.

        Note: recv() *does not* handle KeyboardInterruption exceptions,
        please make sure your calling code does.

        Returns:
            A tuple consisting of:
            - The ControlRequest received, and
            - The appropriate protobuf message (if applicable; if not, None).
            If no request was received, both will be None.
        """
        msg = None
        if self.poll_timeout_ms:
            if self.server.poll(self.poll_timeout_ms, zmq.POLLIN):
                msg = self.server.recv_multipart(zmq.NOBLOCK)
        else:
            msg = self.server.recv_multipart()

        if msg:
            req, obj = cmd.parse_request(msg)
            logger.debug("Message received: %s, %s",
                         common.get_enum_str(ctrl.ControlRequest, req), obj)
            return (req, obj)
        return (None, None)

    def reply(self, rep: ctrl.ControlResponse):
        """Send the reply to a request received.

        This method is expected to be called right after receiving a req.

        Args:
            rep: ctrl.ControlResponse we wish to send as response to the prior
                req received.
        """
        logger.debug("Sending reply: %s",
                     common.get_enum_str(ctrl.ControlResponse, rep))
        self.server.send(cmd.serialize_response(rep))
