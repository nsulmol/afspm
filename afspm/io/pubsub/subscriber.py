""" Holds our Subscriber logic."""

from typing import Callable
from collections.abc import Iterable
import logging
import zmq
from google.protobuf.message import Message

from .. import common

logger = logging.getLogger(__name__)

class Subscriber:
    """Encapsulates subscriber node logic.

    More particularly, encapsulates:
    - a topic-to-proto mapping, sub_extract_proto(), to know what protobuf
    message we have received.
    - a caching mechanism, to store the results as we receive them.

    The main accessor is recv(), though you can choose to grab the subscriber
    socket directly for polling externally. In such a scenario, call
    on_message_received() to handle message decoding and caching.

    Regarding the cache: we expect our cache to consist of:
    - keys that are strings, equivalent to the topics we send.
    - values that are iterables. So, even if only storing 1 object, make sure
    it is in an iterable format.

    Lastly: we have a hardcoded KILL_SIGNAL which we check for. If this signal
    is received, we set a member variable to hold this state, and a getter
    method will return True.

    Attributes:
        sub_extract_proto: method which extracts the proto message from a
            message received from the sub. It must therefore know the
            topic-to-proto mapping.
        extract_proto_kwargs: any additional arguments to be fed to
            sub_extract_proto.
        update_cache: method that updates our cache based on
            the provided 'topic' and proto.
        update_cache_kwargs: any additional arguments to be fed to
            update_cache.
        subscriber: the zmq SUB socket for connecting to the publisher.
        cache: the cache, where we store results according to update_cache.
        shutdown_was_requested: bool, indicating whether or not a kill signal
            has been received.
    """

    def __init__(self, sub_url: str,
                 sub_extract_proto: Callable[[list[bytes]], Message],
                 topics_to_sub: list[str],
                 update_cache: Callable[[str, Message,
                                         dict[str, Iterable]],
                                        dict[str, Iterable]],
                 ctx: zmq.Context = None,
                 extract_proto_kwargs: dict = None,
                 update_cache_kwargs: dict = None, **kwargs):
        """Initializes the caching logic and subscribes.

        Args:
            sub_url: the address of the publisher we will subscribe to, in
                zmq format.
            sub_extract_proto: method which extracts the proto message from a
                message received from the sub. It must therefore know the
                topic-to-proto mapping.
            topics_to_sub: list of topics we wish to subscribe to.
            update_cache: method that updates our cache based on
                the provided 'topic' and proto.
            ctx: zmq Context; if not provided, we will create a new instance.
            extract_proto_kwargs: any additional arguments to be fed to
                sub_extract_proto.
            update_cache_kwargs: any additional arguments to be fed to
                update_cache.
            kwargs: allows non-used input arguments to be passed (so we can
                initialize from an unfiltered dict).
        """
        self.sub_extract_proto = sub_extract_proto
        self.extract_proto_kwargs = (extract_proto_kwargs if
                                     extract_proto_kwargs else {})
        self.update_cache = update_cache
        self.update_cache_kwargs = (update_cache_kwargs if
                                    update_cache_kwargs else {})

        if not ctx:
            ctx = zmq.Context.instance()

        self.subscriber = ctx.socket(zmq.SUB)
        self.subscriber.connect(sub_url)

        # Subscribe to all our topics
        for topic in topics_to_sub:
            self.subscriber.setsockopt(zmq.SUBSCRIBE, topic.encode())
        # Everyone *must* subscribe to the kill signal
        self.subscriber.setsockopt(zmq.SUBSCRIBE, common.KILL_SIGNAL.encode())

        self.cache = {}
        self.shutdown_was_requested = False

    def poll_and_store(self, timeout_ms: int = 1000) -> (str, Message):
        """Receive message and store in cache.

        We use a poll() first, to ensure there is a message to receive.
        To do a blocking receive, simply set timeout_ms to None.

        Note: recv() *does not* handle KeyboardInterruption exceptions,
        please make sure your calling code does.

        Args:
            timeout_ms: the poll timeout, in milliseconds. If None,
                we do not poll and do a blocking receive instead.

        Returns:
            - a tuple containing the envelope/cache key of the message and
                the protobuf.Message received; or
            - None, if no message received.
        """
        msg = None
        if timeout_ms:
            if self.subscriber.poll(timeout_ms, zmq.POLLIN):
                msg = self.subscriber.recv_multipart(zmq.NOBLOCK)
        else:
            msg = self.subscriber.recv_multipart()

        if msg:
            return self.on_message_received(msg)
        return None

    def on_message_received(self, msg: list[bytes]) -> (str, Message):
        """Decode message and update cache.

        Args:
            msg: list of bytes corresponding to the message received by the
                frontend.

        Returns:
            a tuple containing the envelope/cache key of the message and
                the protobuf.Message received. In the case of a KILL signal,
                we return None.
        """
        envelope = msg[0].decode()
        if envelope == common.KILL_SIGNAL:
            logger.info("Shutdown was requested!")
            self.shutdown_was_requested = True
            return None

        proto = self.sub_extract_proto(msg, **self.extract_proto_kwargs)
        logger.debug("Message received %s", envelope)
        self.update_cache(proto, self.cache,
                          **self.update_cache_kwargs)
        return envelope, proto


    def was_shutdown_requested(self):
        """Returns if a shutdown was requested."""
        return self.shutdown_was_requested
