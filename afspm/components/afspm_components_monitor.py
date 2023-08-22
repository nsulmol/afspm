"""Holds the components monitoring helper class."""

import logging
import time
from typing import Callable
import multiprocessing as mp
import zmq

from ..io.heartbeat.heartbeat import HeartbeatListener
from .afspm_component import AfspmComponent, get_heartbeat_url
from ..utils.parser import construct_and_run_component


logger = logging.getLogger(__name__)


class AfspmComponentsMonitor:
    """Monitoring class to startup components and restart them if they crash.

    This class receives a dict of dicts, with each key corresponding to a
    component's name and the value being a dict with the parameters needed to
    instantiate it.

    For example:
    'SampleAfspmComponent': {
        class: 'afspm.components.afspm_component.AfspmComponent',
        loop_sleep_s: 0,
        hb_period_s: 5,
        subscriber: {
            class: 'afspm.io.pubsub.subscriber.Subscriber',
            sub_url: 'tcp://127.0.0.1:5555'
            sub_extract_proto:
            'afspm.io.cache.cache_logic.CacheLogic.create_envelope_from_proto',
            [...]
        }
    }

    Note this is not an immediate kwargs dict; we can use the extra information
    (class key) to recursively spawn objects from the dict's 'leaf' nodes
    upward. The reason why these objects are not already instantiated is that
    we instantiate everything *in the new process* to avoid extra memory usage
    and any potential shared object issues with 3rd party libraries.

    It spawns some or all of these, starting each one as a separate process,
    and monitors their liveliness via a HeartbeatListener. If any component
    freezes or crashes before sending a KILL signal (indicating they planned to
    exit), it will destroy the previous process and restart a new one.

    Note that each HeartbeatListener is instantiated using the associated
    component's Hearbeater.beat_period_s; only missed_beats_before_dead
    is set by the constructor. This allows different frequencies per component.

    Attributes:
        ctx: the zmq.Context instance.
        missed_beats_before_dead: how many missed beats we will allow
            before we consider the Heartbeater dead.
        loop_sleep_s: how many seconds we sleep for between every loop.
        component_params_dict: a dict of the component constructor params, with
            the component.name being used as a key. The format of this dict
            is the same as the input provided to the constructor.
        component_processes_dict: a dict of the currently running processes,
            with the component.name being used as a key.
        listeners_dict: a dict of the currently running HeartbeatListeners,
            with the component.name being used as a key.
    """
    def __init__(self,
                 component_params_dict: dict[str, dict],
                 loop_sleep_s: float, missed_beats_before_dead: int = 5,
                 ctx: zmq.Context = None, **kwargs):
        """Initialize the components monitor.

        Args:
            component_params_dict: a dict of key:vals where the key is a
                component's name and the val is a dict of construction
                parameters.
            loop_sleep_s: how many seconds we sleep for between every loop.
            missed_beats_before_dead: how many missed beats we will allow
                before we consider the Heartbeater dead.
            ctx: the zmq.Context instance.
            kwargs: allows non-used input arguments to be passed (so we can
                initialize from an unfiltered dict).
        """
        if not ctx:
            ctx = zmq.Context.instance()
        self.ctx = ctx

        self.component_params_dict = component_params_dict
        self.loop_sleep_s = loop_sleep_s
        self.missed_beats_before_dead = missed_beats_before_dead

        self.component_processes = {}
        self.listeners = {}
        # Note: starting up of the processes and listeners is in run()

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
            for __, process in self.component_processes.items():
                process.terminate()
        # Not calling super().__del__() because there is no super.

    @staticmethod
    def _startup_component(params_dict: dict, ctx: zmq.Context) -> mp.Process:
        """Start up an AfspmComponent in a Process.

        Args:
            params_dict: dictionary of parameters to feed the constructor.
            ctx: zmq.Context instance.

        Returns:
            Process spawned.
        """
        params_dict['ctx'] = ctx
        proc = mp.Process(target=construct_and_run_component,
                          kwargs={'params_dict': params_dict},
                          daemon=True)  # Ensures we try to kill on main exit
        proc.start()
        return proc

    @staticmethod
    def _startup_listener(params_dict: dict, missed_beats_before_dead: int,
                          ctx: zmq.Context) -> HeartbeatListener:
        """Start up a HeartbeatListener to monitor an AfspmComponent.

        Args:
            params_dict: dictionary of parameters to feed the
                AfspmComponent's constructor.
            missed_beats_before_dead: how many missed beats we will allow
                before we consider the Heartbeater dead.
            ctx: zmq.Context instance.

        Returns:
            The created HeartbeatListener instance.
        """
        name = params_dict['name']
        hb_period_s = params_dict['hb_period_s']
        hb_url = get_heartbeat_url(name)
        return HeartbeatListener(hb_url, hb_period_s,
                                 missed_beats_before_dead, ctx)

    def _startup_processes_and_listeners(self):
        """Startup component processes and their associated listeners."""
        for name in self.component_params_dict:
            self.component_processes[name] = self._startup_component(
                self.component_params_dict[name], self.ctx)
            self.listeners[name] = self._startup_listener(
                self.component_params_dict[name],
                self.missed_beats_before_dead, self.ctx)

    def run(self):
        """Main loop."""
        self._startup_processes_and_listeners()
        continue_running = True
        while continue_running:
            try:
                self.run_per_loop()
                time.sleep(self.loop_sleep_s)
                if not self.component_processes and not self.listeners:
                    logger.info("All components closed, exiting.")
                    continue_running = False
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
                        self.component_params_dict[key], self.ctx)

        # Delete any keys set up for deletion
        for key in keys_to_be_deleted:
            del self.component_processes[key]
            del self.component_params_dict[key]
            del self.listeners[key]
