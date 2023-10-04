"""Manages communication between DeviceController and multiple clients."""

import logging
import zmq
from google.protobuf.message import Message

from . import component as afspmc

from ...io import common
from ...io.pubsub import cache as pbc
from ...io.control import router as ctrl_rtr

from ...io.protos.generated import scan_pb2
from ...io.protos.generated import control_pb2


logger = logging.getLogger(__name__)


class AfspmController(afspmc.AfspmComponent):
    """Manages communication between DeviceController and multiple clients.

    The AfspmController serves as an intermediary between the DeviceController
    and one or more clients. It has 2 main constituents:
    1. A PubSubCache instance, to store information published by the
    DeviceController and publish them to any new subscribers (to ensure all
    subscribers are up-to-date regardless of their subscription time).
    2. A ControlRouter instance, to ensure only one client is sending control
    requests to the DeviceController at a time; to allow switching between
    different ControlModes (e.g. MANUAL, so the automation is paused); and to
    manage ExperimentProblems logged by any client (including ones not in
    control).

    Attributes:
        pubsubcache: PubSubCache instance, for caching data received.
        router: ControlRouter instance, for choosing between clients.
        control_state: holds the last sent control state, used to update
            and determine if a new message is to be sent out (via the
            publisher).
    """
    def __init__(self, name: str,
                 pubsubcache: pbc.PubSubCache,
                 router: ctrl_rtr.ControlRouter,
                 loop_sleep_s: float = common.LOOP_SLEEP_S,
                 hb_period_s: float = common.HEARTBEAT_PERIOD_S,
                 ctx: zmq.Context = None, **kwargs):
        """Initialize AfspmController instance.

        Args:
            name: component name.
            loop_sleep_s: how long we sleep in our main loop.
            hb_period_s: how frequently we should send a hearbeat.
            pubsubcache: PubSubCache instance, for caching data received.
            router: ControlRouter instance, for choosing between clients.
            ctx: zmq context.
            kwargs: allows non-used input arguments to be passed (so we can
                initialize from an unfiltered dict).
        """
        if not ctx:
            ctx = zmq.Context.instance()

        self.pubsubcache = pubsubcache
        self.router = router
        self.control_state = control_pb2.ControlState()
        # AfspmComponent constructor: no subscriber or control_client
        # are provided, as they are not applicable here.
        super().__init__(name, subscriber=None, control_client=None, ctx=ctx,
                         loop_sleep_s=loop_sleep_s, hb_period_s=hb_period_s)

    def run_per_loop(self):  # TODO: Change this to be private everywhere!?
        """Internal checks to be done per loop in run().

        Here, we update the pubsubcache, router.
        """
        self.pubsubcache.poll()
        self.router.poll_and_handle()
        self._handle_send_control_state()
        self._handle_shutdown()

    def _handle_send_control_state(self):
        """Check if a ControlState message needs to be sent (and do if so)."""
        new_control_state = self.router.get_control_state()

        if new_control_state != self.control_state:
            logger.debug("Sending new control state: %s", new_control_state)
            self.pubsubcache.send_message(new_control_state)
        self.control_state = new_control_state

    def _handle_shutdown(self):
        """Determine if a shutdown request was received and send if so.

        We check if the request was sent by a ControlClient, and advertise the
        fact via the publisher (i.e. pubsubcache).
        """
        if self.router.was_shutdown_requested():
            logger.info("Shutdown requested, sending kill signal out.")
            self.pubsubcache.send_kill_signal()
            self.heartbeater.handle_closing()
            self.stay_alive = False  # shutdown self
