"""Holds base class for all components"""

import logging
import time
import tempfile
import zmq

from google.protobuf.message import Message

from ...io import common
from ...io.heartbeat.heartbeat import Heartbeater
from ...io.pubsub import subscriber as sub
from ...io.control import client as ctrl_client


logger = logging.getLogger(__name__)


class AfspmComponent:
    """Base class for afspm component.

    This serves as the base class for any component added to an afspm system.
    It handles behind-the-scenes logic, so that a component stays alive for
    the duration of the experiment and shuts down following its end.

    It consists of the following main constituents:
    - A Heartbeater instance, to indicate to any listeners when this component
    is alive. This allows a listener (e.g. AfspmComponentsMonitor) to restart
    it on crashing/freezing.
    - A Subscriber instance, to receive data from the DeviceController. Most
    importantly, we receive KILL signals from this subscription, to tell us
    when we should shutdown.
    - A ControlClient instance, to allow sending requests to the SPM device.

    The main AfspmComponent method is run(), which is a blocking function that
    continues running for the lifetime of the component. Linked to it is the
    abstract method run_per_loop(); this is the method any child classes should
    override to add logic to-be-run per loop.

    On startup, an AfspmComponent will open a Heartbeater node with url
    "ipc://$NAME$", where $NAME$ is the component name, assumed to be a uuid
    for the duration of the experiment

    Attributes:
        name: the chosen component 'name'.
        heartbeater: Heatbeater instance, to send heartbeats to any monitoring
            listener.
        loop_sleep_s: how long the component sleeps between loops in its main
            loop.
        subscriber: subscriber instance, to receive data from the
            DeviceController. Note: unless you really know what you are doing,
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
                 ctx: zmq.Context = None):
        """Initialize our AfspmComponent.

        Args:
            name: str, chosen to function as the component's uuid.
            loop_sleep_s: how long we sleep in our main loop.
            beat_period_s: how frequently we should send a hearbeat.
            subscriber: subscriber instance, to receive data from the
                DeviceController.
            control_client: client to ControlServer, to allow sending requests.
            ctx: zmq context.
        """
        logger.debug("Initializing component %s", name)
        if not ctx:
            ctx = zmq.Context.instance()

        self.name = name
        hb_url = get_heartbeat_url(self.name)
        self.heartbeater = Heartbeater(hb_url, beat_period_s, ctx)
        self.loop_sleep_s = loop_sleep_s
        self.subscriber = subscriber
        self.control_client = control_client
        self.stay_alive = True

    def run(self):
        """Main loop."""
        logger.info("Starting main loop for component %s", self.name)

        try:
            while self.stay_alive:
                self.heartbeater.handle_beat()
                self._handle_subscriber()
                self.run_per_loop()
                time.sleep(self.loop_sleep_s)
        except (KeyboardInterrupt, SystemExit):
            logger.warning("%s: Interrupt received. Stopping.", self.name)

    def _handle_subscriber(self):
        """Poll subscriber and check for a shutdown request.

        This handler will poll the subscriber (if one was provided for this
        instance). If so, it will also check the
        """
        if self.subscriber:
            msg = self.subscriber.poll_and_store()

            # If the last value indicates shutdown was requested, stop
            # looping
            if self.subscriber.was_shutdown_requested():
                logger.info("%s: Shutdown received. Stopping.", self.name)
                self.heartbeater.handle_closing()
                self.stay_alive = False  # Shutdown self
            elif msg:
                self.on_message_received(msg[0], msg[1])


    def run_per_loop(self):
        """Method that is run on every iteration of the main loop.

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


def get_heartbeat_url(name: str):
    """Create a hearbeat url, given a component name."""
    return "ipc://" + tempfile.gettempdir() + '/' + name
