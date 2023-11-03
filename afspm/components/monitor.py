"""Holds the components monitoring helper class."""

import copy
import logging
import time
from typing import Callable
import multiprocessing as mp
import zmq

from ..io import common
from ..io.heartbeat.heartbeat import HeartbeatListener, get_heartbeat_url
from ..utils.parser import construct_and_run_component


logger = logging.getLogger(__name__)


class AfspmComponentsMonitor:
    """Monitoring class to startup components and restart them if they crash.

    This class receives a dict of dicts, with each key corresponding to a
    component's name and the value being a dict with the parameters needed to
    instantiate it.

    For example:
    'SampleAfspmComponent': {
        class: 'afspm.components.component.AfspmComponent',
        loop_sleep_s: 0,
        beat_period_s: 5,
        subscriber: {
            class: 'afspm.io.pubsub.subscriber.Subscriber',
            sub_url: 'tcp://127.0.0.1:5555'
            sub_extract_proto:
            'afspm.io.pubsub.logic.cache_logic.CacheLogic.get_envelope_for_proto',
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
                 poll_timeout_ms: int = common.POLL_TIMEOUT_MS,
                 loop_sleep_s: float = common.LOOP_SLEEP_S,
                 missed_beats_before_dead: int = common.BEATS_BEFORE_DEAD,
                 ctx: zmq.Context = None):
        """Initialize the components monitor.

        Args:
            component_params_dict: a dict of key:vals where the key is a
                component's name and the val is a dict of construction
                parameters.
            poll_timeout_ms: how long to wait when polling the listener.
            loop_sleep_s: how many seconds we sleep for between every loop.
            missed_beats_before_dead: how many missed beats we will allow
                before we consider the Heartbeater dead.
            ctx: the zmq.Context instance.
        """
        logger.debug("Initializing components monitor.")
        if not ctx:
            ctx = zmq.Context.instance()
        self.ctx = ctx

        self.component_params_dict = component_params_dict
        self.poll_timeout_ms = poll_timeout_ms
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
    def _startup_component(params_dict: dict) -> mp.Process:
        """Start up an AfspmComponent in a Process.

        Note: This method *does not* feed a zmq context! The zmq guide
        explicits 1 (and only 1) context *per process*. Thus, since we
        are instantiating a new process, we want it to create its own
        context.

        In fact, feeding the parent process's context causes bugs between
        ipc sockets of more than 1 child process. So it is crucial to
        spawn a new context for every process.

        Args:
            params_dict: dictionary of parameters to feed the constructor.

        Returns:
            Process spawned.
        """
        params_dict = copy.deepcopy(params_dict)
        params_dict['ctx'] = None
        logger.info("Creating process for component %s", params_dict['name'])
        proc = mp.Process(target=construct_and_run_component,
                          kwargs={'params_dict': params_dict},
                          daemon=True)  # Ensures we try to kill on main exit
        proc.start()
        return proc

    @staticmethod
    def _startup_listener(params_dict: dict, missed_beats_before_dead: int,
                          poll_timeout_ms: int, ctx: zmq.Context
                          ) -> HeartbeatListener:
        """Start up a HeartbeatListener to monitor an AfspmComponent.

        Args:
            params_dict: dictionary of parameters to feed the
                AfspmComponent's constructor.
            missed_beats_before_dead: how many missed beats we will allow
                before we consider the Heartbeater dead.
            poll_timeout_ms: the polling timeout for the listener.
            ctx: zmq.Context instance.

        Returns:
            The created HeartbeatListener instance.
        """
        params_dict = copy.deepcopy(params_dict)
        params_dict['url'] = get_heartbeat_url(params_dict['name'])
        params_dict['poll_timeout_ms'] = poll_timeout_ms

        logger.info("Creating listener for component %s", params_dict['name'])
        return HeartbeatListener(**params_dict)

    def _startup_processes_and_listeners(self) -> bool:
        """Startup component processes and their associated listeners."""
        succeeded = True
        for name in self.component_params_dict:
            self.component_processes[name] = self._startup_component(
                self.component_params_dict[name])
            # Starting listener second because it will wait for startup
            # time (_startup_component() will not, due to it spawning a
            # process).
            self.listeners[name] = self._startup_listener(
                self.component_params_dict[name],
                self.missed_beats_before_dead,
                self.poll_timeout_ms, self.ctx)


            # wait until we get our first heartbeat
            is_alive = True
            while (not self.listeners[name].received_first_beat and is_alive):
                is_alive = self.listeners[name].check_is_alive()

            if not self.listeners[name].received_first_beat:
                logger.info("Component %s failed on start up, exiting.",
                            name)
                succeeded = False
                break
            logger.debug("Received heartbeat for component %s, continuing.",
                         name)

        if not succeeded:
            keys = list(self.listeners.keys())
            for key in keys:
                self._remove_process(key)
        return succeeded

    def run(self):
        """Main loop."""
        logger.info("Starting main loop for components monitor.")
        continue_running = self._startup_processes_and_listeners()
        try:
            while continue_running:
                self.run_per_loop()
                time.sleep(self.loop_sleep_s)
                if not self.component_processes and not self.listeners:
                    logger.info("All components closed, exiting.")
                    continue_running = False
        except (KeyboardInterrupt, SystemExit):
            logger.warning("Interrupt received. Stopping.")

    def run_per_loop(self):
        """The method that is run on every iteration of the main loop.

        We monitor every listener to see if it's associated heartbeat indicates
        it has died/frozen. If it stopped intentionally, we get rid of our
        reference to it and kill the associated listener. If unintentional,
        we respawn the component.
        """
        procs_to_be_removed = []
        for key in self.listeners:
            if not self.listeners[key].check_is_alive():
                if self.listeners[key].received_kill_signal:
                    logger.info("Component %s has finished. Closing.", key)

                    procs_to_be_removed.append(key)
                else:
                    logger.error("Component %s has crashed/frozen. Restarting.",
                                 key)
                    self._restart_process(key)

        # Delete any keys set up for deletion (removed after, to not ruin for
        # loop)
        for key in procs_to_be_removed:
            self._remove_process(key)

    def _restart_process(self, key: str):
        """Restart the process with the provided key (and reset listener)."""
        self.component_processes[key].terminate()
        self.listeners[key].reset()
        self.component_processes[key] = self._startup_component(
            self.component_params_dict[key])

    def _remove_process(self, key: str):
        """Terminate the process and listener with the provided key."""
        self.component_processes[key].terminate()
        del self.component_processes[key]
        del self.component_params_dict[key]
        del self.listeners[key]
