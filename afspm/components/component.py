"""Holds base class for all components"""

import logging
import time
from typing import Callable
import zmq

from google.protobuf.message import Message

from ..io import common
from ..io.heartbeat.heartbeat import Heartbeater, get_heartbeat_url
from ..io.pubsub import subscriber as sub
from ..io.pubsub import publisher as pub
from ..io.control import client as ctrl_client


logger = logging.getLogger(__name__)


class AfspmComponentBase:
    """Base class for afspm component.

    This serves as the base class for any component added to an afspm system.
    It handles behind-the-scenes logic, so that a component stays alive for
    the duration of the experiment and shuts down following its end.

    It consists of the following main constituents:
    - A Heartbeater instance, to indicate to any listeners when this component
    is alive. This allows a listener (e.g. AfspmComponentsMonitor) to restart
    it on crashing/freezing.
    - A Subscriber instance, to receive data from the MicroscopeTranslator. Most
    importantly, we receive KILL signals from this subscription, to tell us
    when we should shutdown.
    - A ControlClient instance, to allow sending requests to the SPM device.

    The main AfspmComponent method is run(), which is a blocking function that
    continues running for the lifetime of the component. Linked to it is the
    abstract method run_per_loop(); this is the method any child classes should
    override to add logic to-be-run per loop.

    On startup, an AfspmComponent will open a Heartbeater node with url
    "ipc://$TMP$/$NAME$", where $NAME$ is the component name (assumed to be a
    uuid for the duration of the experiment) and $TMP$ is a temporary directory
    defined by the OS.

    Attributes:
        ctx: ZMQ context, kept so we can force-close everything when ending.
        name: the chosen component 'name'.
        heartbeater: Heartbeater instance, to send heartbeats to any monitoring
            listener.
        loop_sleep_s: how long the component sleeps between loops in its main
            loop.
        subscriber: subscriber instance, to receive data from the
            MicroscopeTranslator. Note: unless you really know what you are doing,
            use a subscriber - i.e. do not consider it optional.
        control_client: client to ControlServer, to allow sending requests.
        stay_alive: boolean indicating whether we should continue looping in
            run(). In other words, if False, run() ends.
    """

    def __init__(self, name: str,
                 subscriber: sub.Subscriber = None,
                 control_client: ctrl_client.ControlClient = None,
                 loop_sleep_s: float = common.LOOP_SLEEP_S,
                 beat_period_s: float = common.HEARTBEAT_PERIOD_S,
                 override_client_uuid: bool = True,
                 ctx: zmq.Context = None):
        """Initialize our AfspmComponent.

        Args:
            name: str, chosen to function as the component's uuid.
            loop_sleep_s: how long we sleep in our main loop.
            beat_period_s: how frequently we should send a hearbeat.
            subscriber: subscriber instance, to receive data from the
                MicroscopeTranslator.
            control_client: client to ControlServer, to allow sending requests.
            override_client_uuid: boolean indicating whether we will restart
                the provided ControlClient with the component's name as its
                UUID. Default is true.
            ctx: zmq context.
        """
        logger.debug(f"Initializing component {name}")
        if not ctx:
            ctx = zmq.Context.instance()

        self.ctx = ctx
        self.name = name
        hb_url = get_heartbeat_url(self.name)
        self.heartbeater = Heartbeater(hb_url, beat_period_s, ctx)
        self.loop_sleep_s = loop_sleep_s
        self.subscriber = subscriber
        self.control_client = control_client
        self.stay_alive = True

        if self.control_client and override_client_uuid:
            self.control_client.set_uuid(self.name)

    def run(self):
        """Loop."""
        logger.info(f"Starting main loop for component {self.name}")

        try:
            while self.stay_alive:
                self.heartbeater.handle_beat()
                self._handle_subscriber()
                self.run_per_loop()
                time.sleep(self.loop_sleep_s)
        except (KeyboardInterrupt, SystemExit):
            logger.warning(f"{self.name}: Interrupt received. Stopping.")

        # Terminate (not so gracefully)
        self.ctx.destroy()  # TODO: investigate ctx.term() instead.

    def _handle_subscriber(self):
        """Poll subscriber and check for a shutdown request.

        This handler will poll the subscriber (if one was provided for this
        instance). If so, it will also check the
        """
        if self.subscriber:
            messages = self.subscriber.poll_and_store()

            # If the last value indicates shutdown was requested, stop
            # looping
            if self.subscriber.shutdown_was_requested:
                logger.info(f"{self.name}: Shutdown received. Stopping.")
                self.heartbeater.handle_closing()
                self.stay_alive = False  # Shutdown self
            elif messages:
                for msg in messages:
                    self.on_message_received(msg[0], msg[1])

    def run_per_loop(self):
        """Run on every iteration of the main loop.

        If you would like to implement any *general* logic to perform every
        loop, override this method. (Avoid modifying run(), as this handles
        base AfspmComponent logic.)
        """
        pass

    def on_message_received(self, envelope: str, proto: Message):
        """Perform some action on message receipt.

        This method will be called whenever a message is received from the
        subscriber. Overriding it is a good way to perform actions on data
        received.

        Note that the returned envelop and proto correspond to the
        *just received* data, which will be stored in the cache in the key:val
        pair envelope:proto. Other stored data can (of course) be accessed
        from the cache within this method.

        Args:
            envelope: string corresponding to the cache key where this proto
                is stored in the cache.
            proto: the protobuf.Message instance received.
        """
        pass


class AfspmComponent(AfspmComponentBase):
    """Component with hooks for external methods to be called.

    An AfspmComponent differs from AfspmComponentBase in that:
    - An optional publisher can be provided, to allow publishing analysis of
    received messages.
    - An optional callable message_received_method() can be provided, which is
    called in on_message_received().
    - An optional callable per_loop_method() can be provided, which is called
    in run_per_loop().

    The latter two allow component logic to be controlled via some simple
    methods provided (rather than having to define a new class).

    Attributes:
        publisher: publisher instance, to be used to publish analysis results.
        message_received_method: method called on_message_received(), used to
            perform analysis/actions based on new messages.
        per_loop_method: method called oin run_per_loop(), used to perform any
            additional logic desired while the component is running.
        methods_kwargs: any additional arguments to be fed to
            message_received_method and per_loop_method.
    """

    def __init__(self, publisher: pub.Publisher = None,
                 message_received_method: Callable = None,
                 per_loop_method: Callable = None,
                 methods_kwargs: dict = None,
                 **kwargs):
        """Init local variables."""
        self.publisher = publisher
        self.message_received_method = message_received_method
        self.methods_kwargs = (methods_kwargs if
                               methods_kwargs else {})
        self.per_loop_method = per_loop_method

        super().__init__(**kwargs)

    def run_per_loop(self):
        """Override run per_loop_method."""
        if self.per_loop_method:
            self.per_loop_method(self, **self.methods_kwargs)

    def on_message_received(self, envelope: str, proto: Message):
        """Override to run message_received_method."""
        if self.message_received_method:
            self.message_received_method(self, envelope, proto,
                                         **self.methods_kwargs)
