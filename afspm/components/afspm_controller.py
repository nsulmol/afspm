"""Manages communication between DeviceController and multiple clients."""

import logging
import zmq
from google.protobuf.message import Message

from . import afspm_component as afspmc

from ..io.pubsub import pubsubcache as pbc
from ..io.control import control_router as ctrl_rtr

from ..io.protos.generated import scan_pb2 as scan
from ..io.protos.generated import control_pb2 as ctrl


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
        poll_timeout_ms: how long to wait when polling for the different
            components.
        control_state: holds the last sent control state, used to update
            and determine if a new message is to be sent out (via the
            publisher).
    """
    # TODO: update input parameters to remove afspm_component ones
    def __init__(self, name: str, loop_sleep_s: float, hb_period_s: float,
                 pubsubcache: pbc.PubSubCache,
                 router: ctrl_rtr.ControlRouter,
                 poll_timeout_ms: int,
                 ctx: zmq.Context = None, **kwargs):
        """Initialize AfspmController instance.

        Args:
            name: component name.
            loop_sleep_s: how long we sleep in our main loop.
            hb_period_s: how frequently we should send a hearbeat.
            pubsubcache: PubSubCache instance, for caching data received.
            router: ControlRouter instance, for choosing between clients.
            poll_timeout_ms: how long to wait when polling for the different
                components.
            ctx: zmq context.
            kwargs: allows non-used input arguments to be passed (so we can
                initialize from an unfiltered dict).
        """
        if not ctx:
            ctx = zmq.Context.instance()

        self.pubsubcache = pubsubcache
        self.router = router
        self.poll_timeout_ms = poll_timeout_ms
        self.control_state = ctrl.ControlState()
        # AfspmComponent constructor: no subscriber or control_client
        # are provided, as they are not applicable here.
        super().__init__(name, loop_sleep_s, hb_period_s,
                         self.poll_timeout_ms, subscriber=None,
                         control_client=None, ctx=ctx)

    def run_per_loop(self):  # TODO: Change this to be private everywhere!?
        """Internal checks to be done per loop in run().

        Here, we update the pubsubcache, router.
        """
        self.pubsubcache.poll(self.poll_timeout_ms)
        self.router.poll_and_handle(self.poll_timeout_ms)
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
