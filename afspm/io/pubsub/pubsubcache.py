""" Holds our PubSubCache logic."""

from typing import Callable
from collections.abc import Iterable
import logging
import zmq
from google.protobuf.message import Message

from .. import common

logger = logging.getLogger(__name__)


class PubSubCache:
    """Handles data caching between publisher and subscribers.

    This class sits between a publisher and a list of subscribers,
    allowing caching of envelopes. It is a slightly more open variant of
    Last Value Caching (LVC), where only the last value is cached per
    envelope.

    In this implementation, a method update_cache() is provided to
    determine how data is cached.

    Since this cache sits in between a publisher and subscribers, it will
    receive [envelope, proto] messages from the publisher and will package
    these into *potentially separate* [new_envelope, proto] messages to the
    subscribers.
    This permits more complex logic than a 1-to-1 mapping between proto and
    envelope. For example, one may decide to have 2 envelopes defined for a
    given proto 'type' containing a region scan: one 'zoomed out' envelope,
    and the other 'zoomed in'.

    To allow this, we require two more methods:
    - sub_extract_proto() knows the envelope-to-proto mapping on the subscriber
    end, so it can extract any proto as received.
    - pub_get_envelope_given_proto() defines *this cache's* mapping from proto
    to envelope.

    The main accessor is poll(). If you instead want to include this class's
    sockets into an external poller for polling, you can do so. In that case,
    you will want to call on_message_received() and on_new_subscription() for
    the frontend and backend, respectively.

    Regarding the cache: we expect our cache to consist of:
    - keys that are strings, equivalent to the envelopes we send.
    - values that are iterables. So, even if only storing 1 object, make sure
    it is in an iterable format.

    Note: this implementation was inspired by the lvcache example in the zeromq
    guide (Chapter 5).

    Attributes:
        sub_extract_proto: method which extracts the proto message from a
            message received from the sub. It must therefore know the
            envelope-to-proto mapping.
        extract_proto_kwargs: any additional arguments to be fed to
            sub_extract_proto.
        pub_get_envelope_given_proto: method that maps from proto message to
            our desired publisher 'envelope' string.
        get_envelope_kwargs: any additional arguments to be fed to
            pub_get_envelope_given_proto.
        update_cache: method that updates our cache.
        update_cache_kwargs: any additional arguments to be fed to
            update_cache.
        frontend: SUB socket connected to the publisher.
        backend: XPUB socket, the publisher end
        cache: the cache, where we store data according to update_cache.
        poller: zmq Poller, to poll frontend and backend.
    """

    def __init__(self, url: str, sub_url: str,
                 sub_extract_proto: Callable[[list[bytes], ...], Message],
                 pub_get_envelope_given_proto: Callable[[Message, ...], str],
                 update_cache: Callable[[str, Message,
                                         dict[str, Iterable], ...],
                                        dict[str, Iterable]],
                 ctx: zmq.Context = None,
                 extract_proto_kwargs: dict = None,
                 get_envelope_kwargs: dict = None,
                 update_cache_kwargs: dict = None, **kwargs):
        """Initializes the caching logic and connects our nodes.

        Args:
            url: the address of our publisher end, in zmq format.
            sub_url: the address of the publisher we will subscribe to, in
                zmq format.
            sub_extract_proto: method which extracts the proto message from a
                message received from the sub. It must therefore know the
                envelope-to-proto mapping.
            pub_get_envelope_given_proto: method that maps from proto message to
                our desired publisher 'envelope' string.
            update_cache: method that updates our cache.
            ctx: zmq Context; if not provided, we will create a new instance.
            extract_proto_kwargs: any additional arguments to be fed to
                sub_extract_proto.
            get_envelope_kwargs: any additional arguments to be fed to
                pub_get_envelope_given_proto.
            update_cache_kwargs: any additional arguments to be fed to
                update_cache.
            kwargs: allows non-used input arguments to be passed (so we can
                initialize from an unfiltered dict).
        """
        self.sub_extract_proto = sub_extract_proto
        self.extract_proto_kwargs = (extract_proto_kwargs if
                                     extract_proto_kwargs else {})
        self.pub_get_envelope_given_proto = pub_get_envelope_given_proto
        self.get_envelope_kwargs = (get_envelope_kwargs if
                                    get_envelope_kwargs else {})
        self.update_cache = update_cache
        self.update_cache_kwargs = (update_cache_kwargs if
                                    update_cache_kwargs else {})

        if not ctx:
            ctx = zmq.Context.instance()

        self.frontend = ctx.socket(zmq.SUB)
        self.frontend.connect(sub_url)

        self.backend = ctx.socket(zmq.XPUB)
        # Receive all subscription notifications
        self.backend.setsockopt(zmq.XPUB_VERBOSE, True)
        self.backend.bind(url)

        # Subscribe to every single envelope from publisher
        self.frontend.setsockopt(zmq.SUBSCRIBE, b"")

        # Initialize our cache
        self.cache = {}

        self.poller = zmq.Poller()
        self.poller.register(self.frontend, zmq.POLLIN)
        self.poller.register(self.backend, zmq.POLLIN)

    def poll(self, timeout_ms: int = 1000):
        """Poll and handle communication between pub and subs.


        Note: poll() *does not* handle KeyboardInterruption exceptions,
        please make sure your calling code does.

        Args:
            timeout_ms: the poll timeout, in milliseconds
        """
        events = dict(self.poller.poll(timeout_ms))

        # Handle subscriptions
        # (when we get a subscription, we pull data from the cache)
        # I think this means we re-send cache data to *everyone* subscribed :/.
        if self.backend in events:
            event = self.backend.recv()
            # Event is one byte 0=unsub or 1=sub, followed by envelope
            if event[0] == 1:
                envelope = event[1:].decode()
                self._on_new_subscription(envelope)

        # Any new envelope data we cache and then forward
        if self.frontend in events:
            msg = self.frontend.recv_multipart()
            self._on_message_received(msg)


    def _on_message_received(self, msg: list[bytes]):
        """Decode message, cache it, and pass on to subscribers.

        Args:
            msg: list of bytes corresponding to the message received by the
                frontend.
        """
        proto = self.sub_extract_proto(msg, **self.extract_proto_kwargs)
        return self.send_message(proto)

    def _on_new_subscription(self, envelope: str):
        """Send associated cache (if envelope exists).

        If envelope exists in cache, send back the items associated with it
        one at a time.

        Args:
            envelope: the subscribed envelope.
        """
        envelope_log = (common.ALL_ENVELOPE_LOG
                        if envelope == common.ALL_ENVELOPE else envelope)
        logger.info("New subscription to %s.", envelope_log)

        # If "ALL" subscribed, send all envelopes in our cache
        envelopes_to_send = (list(self.cache.keys())
                             if envelope == common.ALL_ENVELOPE
                             else [envelope])
        for env in envelopes_to_send:
            if env in self.cache:
                logger.info("Subscription: cache for %s being sent out.",
                            env)
                for proto in self.cache[env]:
                    self.backend.send_multipart([env.encode(),
                                                 proto.SerializeToString()])

    def send_message(self, proto: Message):
        """Cache message and pass on to subscribers.

        Args:
            proto: protobuf Message.
        """
        # TODO: pub_get_envelope not needed, since update cache does it???
        # Or do we keep it in case?

        envelope = self.pub_get_envelope_given_proto(
            proto, **self.get_envelope_kwargs)
        self.update_cache(proto, self.cache,
                          **self.update_cache_kwargs)
        logger.debug("Sending message %s", envelope)
        self.backend.send_multipart([envelope.encode(),
                                     proto.SerializeToString()])

    def send_kill_signal(self):
        """Send a kill signal to subscribers."""
        logger.debug("Sending kill signal.")
        self.backend.send_multipart([common.KILL_SIGNAL.encode()])
