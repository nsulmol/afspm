"""Holds our Publisher logic."""

from typing import Callable
import logging

import zmq

from google.protobuf.message import Message

from .. import common
from . import defaults


logger = logging.getLogger(__name__)


class Publisher:
    """Encapsulates publisher node logic.

    More particularly, this encapsulates the proto-to-envelope mapping
    (get_envelope_for_proto), so the method using it can simply feed the
    desired proto.

    Attributes:
        _publisher: the zmq PUB socket for sending messages out.
        _get_envelope_for_proto: method that maps from proto message to
            our desired publisher 'envelope' string.
        _get_envelope_kwargs: any additional arguments to be fed to
            get_envelope_for_proto.
    """

    def __init__(self, url: str,
                 get_envelope_for_proto: Callable[[Message], str] =
                 defaults.PUBLISHER_ENVELOPE_FOR_PROTO,
                 ctx: zmq.Context = None,
                 get_envelope_kwargs: dict =
                 defaults.PUBLISHER_ENVELOPE_KWARGS):
        """Initialize the publisher.

        Args:
            url: our publishing address, in zmq format.
            get_envelope_for_proto: method that maps from proto message to
                our desired publisher 'envelope' string.
            ctx: zmq Context; if not provided, we will create a new instance.
            get_envelope_kwargs: any additional arguments to be fed to
                get_envelope_for_proto.
        """
        self._get_envelope_for_proto = get_envelope_for_proto
        self._get_envelope_kwargs = (get_envelope_kwargs if get_envelope_kwargs
                                     else {})

        if not ctx:
            ctx = zmq.Context.instance()

        self._publisher = ctx.socket(zmq.PUB)
        self._publisher.setsockopt(zmq.LINGER, 0)  # Never linger on closure
        self._publisher.bind(url)

        common.sleep_on_socket_startup()

    def send_msg(self, proto: Message):
        """Send message via publisher.

        It uses get_envelope_for_proto to determine the envelope of our
        message.

        Args:
            proto: protobuf message to send.
        """
        envelope = self._get_envelope_for_proto(proto,
                                                **self._get_envelope_kwargs)
        logger.debug(f"Sending message {envelope}")
        self._publisher.send_multipart([envelope.encode(),
                                       proto.SerializeToString()])

    def send_kill_signal(self):
        """Send a kill signal to subscribers."""
        logger.debug("Sending kill signal.")
        self._publisher.send_multipart([common.KILL_SIGNAL.encode()])
