"""Holds our PubSubCache logic."""

from typing import Callable
from collections.abc import Iterable
import logging

import zmq

from google.protobuf.message import Message

from .. import common
from . import defaults

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
        cache: the cache, where we store data according to update_cache.

        _sub_extract_proto: method which extracts the proto message from a
            message received from the sub. It must therefore know the
            envelope-to-proto mapping.
        _extract_proto_kwargs: any additional arguments to be fed to
            sub_extract_proto.
        _pub_get_envelope_for_proto: method that maps from proto message to
            our desired publisher 'envelope' string.
        _get_envelope_kwargs: any additional arguments to be fed to
            pub_get_envelope_given_proto.
        _update_cache: method that updates our cache.
        _update_cache_kwargs: any additional arguments to be fed to
            update_cache.
        _poll_timeout_ms: the poll timeout, in milliseconds. If None,
            we do not poll and do a blocking receive instead.

        _frontend: SUB socket connected to the publisher.
        _backend: XPUB socket, the publisher end
        _poller: zmq Poller, to poll frontend and backend.
    """

    def __init__(self, url: str, sub_url: str,
                 sub_extract_proto: Callable[[list[bytes], ...], Message] =
                 defaults.EXTRACT_PROTO,
                 pub_get_envelope_for_proto: Callable[[Message, ...], str] =
                 defaults.PUBSUBCACHE_GET_ENVELOPE_FOR_PROTO,
                 update_cache: Callable[[str, Message,
                                         dict[str, Iterable], ...],
                                        dict[str, Iterable]] =
                 defaults.UPDATE_CACHE,
                 ctx: zmq.Context = None,
                 extract_proto_kwargs: dict =
                 defaults.PUBSUBCACHE_EXTRACT_PROTO_KWARGS,
                 get_envelope_kwargs: dict =
                 defaults.PUBSUBCACHE_GET_ENVELOPE_KWARGS,
                 update_cache_kwargs: dict =
                 defaults.PUBSUBCACHE_UPDATE_CACHE_KWARGS,
                 poll_timeout_ms: int = common.POLL_TIMEOUT_MS):
        """Initialize the caching logic and connects our nodes.

        Args:
            url: the address of our publisher end, in zmq format.
            sub_url: the address of the publisher we will subscribe to, in
                zmq format.
            sub_extract_proto: method which extracts the proto message from a
                message received from the sub. It must therefore know the
                envelope-to-proto mapping.
            pub_get_envelope_for_proto: method that maps from proto message to
                our desired publisher 'envelope' string.
            update_cache: method that updates our cache.
            ctx: zmq Context; if not provided, we will create a new instance.
            extract_proto_kwargs: any additional arguments to be fed to
                sub_extract_proto.
            get_envelope_kwargs: any additional arguments to be fed to
                pub_get_envelope_for_proto.
            update_cache_kwargs: any additional arguments to be fed to
                update_cache.
            poll_timeout_ms: the poll timeout, in milliseconds. If None,
                we do not poll and do a blocking receive instead.
        """
        self._sub_extract_proto = sub_extract_proto
        self._extract_proto_kwargs = (extract_proto_kwargs if
                                      extract_proto_kwargs else {})
        self._pub_get_envelope_for_proto = pub_get_envelope_for_proto
        self._get_envelope_kwargs = (get_envelope_kwargs if
                                     get_envelope_kwargs else {})
        self._update_cache = update_cache
        self._update_cache_kwargs = (update_cache_kwargs if
                                     update_cache_kwargs else {})
        self._poll_timeout_ms = poll_timeout_ms

        if not ctx:
            ctx = zmq.Context.instance()

        self._frontend = ctx.socket(zmq.SUB)
        self._frontend.connect(sub_url)

        self._backend = ctx.socket(zmq.XPUB)
        # Receive all subscription notifications
        self._backend.setsockopt(zmq.XPUB_VERBOSE, True)
        self._backend.bind(url)

        # Subscribe to every single envelope from publisher
        self._frontend.setsockopt(zmq.SUBSCRIBE, b"")

        # Initialize our cache
        self.cache = {}

        self._poller = zmq.Poller()
        self._poller.register(self._frontend, zmq.POLLIN)
        self._poller.register(self._backend, zmq.POLLIN)

        common.sleep_on_socket_startup()

    def poll(self):
        """Poll and handle communication between pub and subs.

        Note: poll() *does not* handle KeyboardInterruption exceptions,
        please make sure your calling code does.
        """
        events = dict(self._poller.poll(self._poll_timeout_ms))

        # Handle subscriptions
        # (when we get a subscription, we pull data from the cache)
        # I think this means we re-send cache data to *everyone* subscribed :/.
        backend_count = (events[self._backend] if self._backend in events
                         else None)
        if backend_count:
            backend_events = []
            for i in range(backend_count):
                backend_events.append(self._backend.recv(zmq.NOBLOCK))

            logger.debug(f'backend_events: {backend_events}')

            for event in backend_events:
                # Event is one byte 0=unsub or 1=sub, followed by envelope
                if event[0] == 1:
                    envelope = event[1:].decode()
                    self._on_new_subscription(envelope)

        # Any new envelope data we cache and then forward
        frontend_count = (events[self._frontend] if self._frontend in events
                          else None)
        if frontend_count:
            frontend_events = []
            for i in range(frontend_count):
                frontend_events.append(self._frontend.recv_multipart())

            for event in frontend_events:
                self._on_message_received(event)

    def _on_message_received(self, msg: list[bytes]):
        """Decode message, cache it, and pass on to subscribers.

        Args:
            msg: list of bytes corresponding to the message received by the
                frontend.
        """
        proto = self._sub_extract_proto(msg, **self._extract_proto_kwargs)
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
        logger.info(f"New subscription to {envelope_log}")

        # If "ALL" subscribed, send all envelopes in our cache
        envelopes_to_send = (list(self.cache.keys())
                             if envelope == common.ALL_ENVELOPE
                             else [envelope])
        for env in envelopes_to_send:
            if env in self.cache:
                logger.info(f"Subscription: cache for {env} being sent out.")
                for proto in self.cache[env]:
                    self._backend.send_multipart([env.encode(),
                                                  proto.SerializeToString()])

    def send_message(self, proto: Message):
        """Cache message and pass on to subscribers.

        Args:
            proto: protobuf Message.
        """
        envelope = self._pub_get_envelope_for_proto(
            proto, **self._get_envelope_kwargs)
        self._update_cache(proto, self.cache,
                           **self._update_cache_kwargs)
        logger.debug(f"Sending message {envelope}")
        self._backend.send_multipart([envelope.encode(),
                                      proto.SerializeToString()])

    def send_kill_signal(self):
        """Send a kill signal to subscribers."""
        logger.debug("Sending kill signal.")
        self._backend.send_multipart([common.KILL_SIGNAL.encode()])
