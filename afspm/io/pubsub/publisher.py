""" Holds our Publisher logic."""

from typing import Callable
from collections.abc import Iterable
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
    """

    def __init__(self, url: str,
                 get_envelope_for_proto: Callable[[Message], str] =
                 defaults.PUBLISHER_ENVELOPE_FOR_PROTO,
                 ctx: zmq.Context = None,
                 get_envelope_kwargs: dict =
                 defaults.PUBLISHER_ENVELOPE_KWARGS, **kwargs):
        """ Initializes the publisher.

        Args:
            url: our publishing address, in zmq format.
            get_envelope_for_proto: method that maps from proto message to
                our desired publisher 'envelope' string.
            ctx: zmq Context; if not provided, we will create a new instance.
            get_envelope_kwargs: any additional arguments to be fed to
                get_envelope_for_proto.
            kwargs: allows non-used input arguments to be passed (so we can
                initialize from an unfiltered dict).
        """
        self.get_envelope_for_proto = get_envelope_for_proto
        self.get_envelope_kwargs = (get_envelope_kwargs if get_envelope_kwargs
                                    else {})

        if not ctx:
            ctx = zmq.Context.instance()

        self.publisher = ctx.socket(zmq.PUB)
        self.publisher.setsockopt(zmq.LINGER, 0)  # Never linger on closure
        self.publisher.bind(url)

        common.sleep_on_socket_startup()


    def send_msg(self, proto: Message):
        """ Send message via publisher.

        It uses get_envelope_for_proto to determine the envelope of our
        message.

        Args:
            proto: protobuf message to send.
        """

        envelope = self.get_envelope_for_proto(proto,
                                               **self.get_envelope_kwargs)
        logger.debug("Sending message %s", envelope)
        self.publisher.send_multipart([envelope.encode(),
                                       proto.SerializeToString()])
