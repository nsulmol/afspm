"""Holds base class for all components (and monitoring helper)."""

import logging
import time
from typing import Callable
import multiprocessing as mp
import zmq

from google.protobuf.message import Message

from ..io.heartbeat.heartbeat import Heartbeater, HeartbeatListener
from ..io.pubsub import subscriber as sub
from ..io.control import control_client as ctrl_client


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
        poll_timeout_ms: how long to wait when polling the subscriber.
        stay_alive: boolean indicating whether we should continue looping in
            run(). In other words, if False, run() ends.
    """

    def __init__(self, name: str, loop_sleep_s: float,
                 hb_period_s: float, poll_timeout_ms: int = 0,
                 subscriber: sub.Subscriber = None,
                 control_client: ctrl_client.ControlClient = None,
                 ctx: zmq.Context = None):
        """Initialize our AfspmComponent.

        Args:
            name: str, chosen to function as the component's uuid.
            loop_sleep_s: how long we sleep in our main loop.
            hb_period_s: how frequently we should send a hearbeat.
            poll_timeout_ms: how long to wait when polling the subscriber.
            subscriber: subscriber instance, to receive data from the
                DeviceController.
            control_client: client to ControlServer, to allow sending requests.
            ctx: zmq context.
        """
        if not ctx:
            ctx = zmq.Context.instance()

        self.name = name
        hb_url = get_heartbeat_url(self.name)
        self.heartbeater = Heartbeater(hb_url, hb_period_s, ctx)
        self.loop_sleep_s = loop_sleep_s
        self.subscriber = subscriber
        self.control_client = control_client
        self.poll_timeout_ms = poll_timeout_ms
        self.stay_alive = True

    def run(self):
        """Main loop."""
        while self.stay_alive:
            try:
                #print(f"Main loop for: {self.name}")
                self.heartbeater.handle_beat()
                self._handle_subscriber()
                self.run_per_loop()
                time.sleep(self.loop_sleep_s)
            except (KeyboardInterrupt, SystemExit):
                logger.warning("%s: Interrupt received. Stopping., self.name")
                self.stay_alive = False

    def _handle_subscriber(self):
        """Poll subscriber and check for a shutdown request.

        This handler will poll the subscriber (if one was provided for this
        instance). If so, it will also check the
        """
        if self.subscriber:
            msg = self.subscriber.poll_and_store(self.poll_timeout_ms)

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


class AfspmComponentsMonitor:
    """Monitoring class to startup components and restart them if they crash.

    This class receives a list of (constructor, kwargs) tuples, for creating
    AfspmComponents. It spawns each of them as separate processes, and monitors
    their liveliness via a HeartbeatListener. If any component freezes or
    crashes before sending a KILL signal (indicating they planned to exit),
    it will destroy the previous process and restart a new one.

    Note that each HeartbeatListener is instantiated using the associated
    component's Hearbeater.beat_period_s; only missed_beats_before_dead
    is set by the constructor. This allows different frequencies per component.

    Attributes:
        ctx: the zmq.Context instance.
        missed_beats_before_dead: how many missed beats we will allow
            before we consider the Heartbeater dead.
        loop_sleep_s: how many seconds we sleep for between every loop.
        component_constructors: a dict of the component constructors, with
            the component.name being used as a key.
        component_kwargs: a dict of the component constructor kwargs, with
            the component.name being used as a key.
        component_processes: a dict of the currently running processes, with
            the component.name being used as a key.
        listeners: a dict of the currently running HeartbeatListeners, with
            the component.name being used as a key.
    """
    def __init__(self,
                 comp_constructor_kwargs_list: list[
                     (Callable[[], AfspmComponent],dict)],
                 loop_sleep_s: float, missed_beats_before_dead: int = 5,
                 ctx: zmq.Context = None):
        """Initialize the components monitor.

        Args:
            comp_constructor_kwargs_list: list of (constructor, kwargs) tuples,
                for constructing the components.
            loop_sleep_s: how many seconds we sleep for between every loop.
            missed_beats_before_dead: how many missed beats we will allow
                before we consider the Heartbeater dead.
            ctx: the zmq.Context isntance.
        """
        if not ctx:
            ctx = zmq.Context.instance()
        self.ctx = ctx

        self.missed_beats_before_dead = missed_beats_before_dead
        self.loop_sleep_s = loop_sleep_s

        # Build up constructor and kwarg dicts (comp name is the key)
        self.component_constructors = {}
        self.component_kwargs = {}
        self.component_processes = {}
        self.listeners = {}
        for (constructor, kwargs) in comp_constructor_kwargs_list:
            name = kwargs['name']
            self.component_constructors[name] = constructor
            self.component_kwargs[name] = kwargs

            self.component_processes[name] = self._startup_component(
                constructor, kwargs, self.ctx)
            self.listeners[name] = self._startup_listener(
                kwargs, self.missed_beats_before_dead, self.ctx)

    def __del__(self):
        """Extra-careful deletion to ensure processes are deleted.

        Without this __del__(), the spawned processes will only be deleted
        once the parent process closes (i.e. the spawning Python process).
        While this is the expected usage of this class, we are being extra
        careful here and explicitly close all linked processes.

        Note: __del__() may be called before __init__() finishes! Thus, we
        ensure our member variable of interest exists before calling on it.
        """
        if self.component_processes:
            for key in self.component_processes:
                self.component_processes[key].terminate()
        # Not calling super().__del__() because there is no super.

    @staticmethod
    def _startup_component(constructor: Callable[[], AfspmComponent],
                           kwargs: dict, ctx: zmq.Context) -> mp.Process:
        """Start up an AfspmComponent in a Process.

        Args:
            constructor: object constructor.
            kwargs: dictionary of kwargs to feed the constructor.
            ctx: zmq.Context instance.

        Returns:
            Process spawned.
        """
        kwargs['ctx'] = ctx
        proc = mp.Process(target=construct_and_run_component,
                          args=(constructor, kwargs),
                          daemon=True)  # Ensures we try to kill on main exit
        proc.start()
        return proc

    @staticmethod
    def _startup_listener(comp_kwargs: dict, missed_beats_before_dead: int,
                          ctx: zmq.Context) -> HeartbeatListener:
        """Start up a HeartbeatListener to monitor an AfspmComponent.

        Args:
            comp_kwarg: kwargs dict of the AfspmComponent's constructor.
            missed_beats_before_dead: how many missed beats we will allow
                before we consider the Heartbeater dead.
            ctx: zmq.Context instance.

        Returns:
            The created HeartbeatListener instance.
        """
        name = comp_kwargs['name']
        hb_period_s = comp_kwargs['hb_period_s']
        hb_url = get_heartbeat_url(name)
        return HeartbeatListener(hb_url, hb_period_s,
                                 missed_beats_before_dead, ctx)

    def run(self):
        """Main loop."""
        while True:
            try:
                self.run_per_loop()
                time.sleep(self.loop_sleep_s)
            except (KeyboardInterrupt, SystemExit):
                logger.warning("Interrupt received. Stopping.")
                break

    def run_per_loop(self):
        """The method that is run on every iteration of the main loop.

        We monitor every listener to see if it's associated heartbeat indicates
        it has died/frozen. If it stopped intentionally, we get rid of our
        reference to it and kill the associated listener. If unintentional,
        we respawn the component.
        """
        keys_to_be_deleted = []
        for key in self.listeners:
            if self.listeners[key].check_if_dead():
                if self.listeners[key].received_kill_signal:
                    logger.info("Component %s has finished. Closing.", key)

                    # Should be dead, terminating as an extra safety.
                    self.component_processes[key].terminate()
                    keys_to_be_deleted.append(key)
                else:
                    logger.info("Component %s has crashed/frozen. Restarting.",
                                key)
                    self.component_processes[key].terminate()
                    self.listeners[key].reset()

                    # New context needed for this socket (since old sockets
                    # may not have been properly closed).
                    self.ctx = zmq.Context()
                    self.component_processes[key] = self._startup_component(
                        self.component_constructors[key],
                        self.component_kwargs[key], self.ctx)

        # Delete any keys set up for deletion
        for key in keys_to_be_deleted:
            del self.component_processes[key]
            del self.component_kwargs[key]
            del self.listeners[key]



def get_heartbeat_url(name: str):
    """Create a hearbeat url, given a component name."""
    return "ipc://" + name


def construct_and_run_component(constructor: Callable[[], AfspmComponent],
                                kwargs: dict):
    """Method to build and run an AfspmComponent, for use with multiprocess.

    This method functions as the process method fed to each mp.Process
    constructor, to start up a component and run it.

    Args:
        constructor: the AfspmComponent's constructor.
        kwargs: a kwargs dict holding the parameters we need to feed to
            the AfspmComponent constructor.
    """
    component = constructor(**kwargs)
    component.run()
