""" Holds our Publisher logic."""

from typing import Callable
from collections.abc import Iterable
import logging
import zmq
from google.protobuf.message import Message

logger = logging.getLogger(__name__)


class Publisher:
    """Encapsulates publisher node logic.

    More particularly, this encapsulates the proto-to-envelope mapping
    (get_envelope_given_proto), so the method using it can simply feed the
    desired proto.
    """

    def __init__(self, url: str,
                 get_envelope_given_proto: Callable[[Message], str],
                 ctx: zmq.Context = None,
                 get_envelope_kwargs: dict = None):
        """ Initializes the publisher.

        Args:
            url: our publishing address, in zmq format.
            get_envelope_given_proto: method that maps from proto message to
                our desired publisher 'envelope' string.
            ctx: zmq Context; if not provided, we will create a new instance.
            get_envelope_kwargs: any additional arguments to be fed to
                get_envelope_given_proto.
        """
        self.get_envelope_given_proto = get_envelope_given_proto
        self.get_envelope_kwargs = (get_envelope_kwargs if get_envelope_kwargs
                                    else {})

        if not ctx:
            ctx = zmq.Context.instance()

        self.publisher = ctx.socket(zmq.PUB)
        self.publisher.bind(url)

    def send_msg(self, proto: Message):
        """ Send message via publisher.

        It uses get_envelope_given_proto to determine the envelope of our
        message.

        Args:
            proto: protobuf message to send.
        """

        envelope = self.get_envelope_given_proto(proto,
                                                 **self.get_envelope_kwargs)
        self.publisher.send_multipart([envelope.encode(),
                                       proto.SerializeToString()])
