"""Holds the components monitoring helper class."""

import logging
import time
from typing import Callable
import multiprocessing as mp
import zmq

from ..io.heartbeat.heartbeat import HeartbeatListener
from .afspm_component import AfspmComponent, get_heartbeat_url


logger = logging.getLogger(__name__)


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
