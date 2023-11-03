"""Test the AfspmComponentsMonitor module logic."""

import copy
import time
import logging
import pytest
import zmq

from afspm.components.afspm.component import AfspmComponentBase
from afspm.components.afspm.monitor import AfspmComponentsMonitor


logger = logging.getLogger(__name__)


# ----- Fixtures ----- #
@pytest.fixture
def comp_name():
    return 'test_component'


@pytest.fixture
def poll_timeout_ms():
    return 25


@pytest.fixture
def loop_sleep_s():
    return 0.05


@pytest.fixture
def beat_period_s():
    return 0.1


@pytest.fixture
def kwargs(comp_name, loop_sleep_s, beat_period_s):
    kwargs_dict = {}
    kwargs_dict['name'] = comp_name
    kwargs_dict['loop_sleep_s'] = loop_sleep_s
    kwargs_dict['beat_period_s'] = beat_period_s
    return kwargs_dict


@pytest.fixture
def missed_beats_before_dead():
    return 5


@pytest.fixture
def ctx():
    return zmq.Context()


@pytest.fixture
def time_to_wait_s(beat_period_s, missed_beats_before_dead):
    return 2 * beat_period_s * missed_beats_before_dead


# ----- Classes for Testing ----- #
class CrashingComponent(AfspmComponentBase):
    """A simple component that crashes after some time."""
    def __init__(self, time_to_crash_s: float, **kwargs):
        self.time_to_crash_s = time_to_crash_s
        self.start_ts = time.time()
        super().__init__(**kwargs)

    def run_per_loop(self):
        curr_ts = time.time()
        if curr_ts - self.start_ts >= self.time_to_crash_s:
            raise SystemExit


class ExitingComponent(AfspmComponentBase):
    """A simple component that exits purposefully after some time."""
    def __init__(self, time_to_exit_s: float, **kwargs):
        self.time_to_exit_s = time_to_exit_s
        self.start_ts = time.time()
        super().__init__(**kwargs)

    def run_per_loop(self):
        curr_ts = time.time()
        if curr_ts - self.start_ts >= self.time_to_exit_s:
            self.heartbeater.handle_closing()
            raise SystemExit


def monitor_and_wait(monitor: AfspmComponentsMonitor,
                     start_ts: float, time_to_wait_s: float,
                     loop_sleep_s: float):
    """Helper to wait and monitor a bit."""
    curr_ts = time.time()
    while curr_ts - start_ts < time_to_wait_s:
        monitor.run_per_loop()
        time.sleep(loop_sleep_s)
        curr_ts = time.time()


# ----- Tests ----- #
def test_basic_component(ctx, kwargs, loop_sleep_s,
                         comp_name, missed_beats_before_dead,
                         time_to_wait_s, poll_timeout_ms):
    """Ensure a standard component stays alive for the test lifetime."""
    kwargs['class'] = 'afspm.components.afspm.component.AfspmComponent'
    components_params_dict = {comp_name: kwargs}
    monitor = AfspmComponentsMonitor(components_params_dict,
                                     poll_timeout_ms,
                                     loop_sleep_s,
                                     missed_beats_before_dead,
                                     ctx)
    monitor._startup_processes_and_listeners()

    assert len(monitor.component_processes) == 1
    assert comp_name in monitor.component_processes
    original_pid = monitor.component_processes[comp_name].pid

    start_ts = time.time()
    monitor_and_wait(monitor, start_ts, time_to_wait_s, loop_sleep_s)

    assert len(monitor.component_processes) == 1
    assert comp_name in monitor.component_processes
    assert original_pid == monitor.component_processes[comp_name].pid


def test_two_basic_components(ctx, kwargs, loop_sleep_s,
                              comp_name, missed_beats_before_dead,
                              time_to_wait_s, poll_timeout_ms):
    """Ensure 2 standard components stay alive for the test lifetime."""
    kwargs['class'] = 'afspm.components.afspm.component.AfspmComponentBase'

    kwargs2 = copy.deepcopy(kwargs)
    comp_name2 = comp_name + "2"
    kwargs2['name'] = comp_name2

    components_params_dict = {comp_name: kwargs,
                              comp_name2: kwargs2}

    monitor = AfspmComponentsMonitor(components_params_dict,
                                     poll_timeout_ms,
                                     loop_sleep_s,
                                     missed_beats_before_dead,
                                     ctx)
    monitor._startup_processes_and_listeners()

    assert len(monitor.component_processes) == 2
    assert comp_name in monitor.component_processes
    assert comp_name2 in monitor.component_processes
    original_pid = monitor.component_processes[comp_name].pid
    original_pid2 = monitor.component_processes[comp_name2].pid

    start_ts = time.time()
    monitor_and_wait(monitor, start_ts, time_to_wait_s, loop_sleep_s)

    assert len(monitor.component_processes) == 2
    assert comp_name in monitor.component_processes
    assert comp_name2 in monitor.component_processes
    assert original_pid == monitor.component_processes[comp_name].pid
    assert original_pid2 == monitor.component_processes[comp_name2].pid


def test_crashing_component(ctx, kwargs, loop_sleep_s, beat_period_s,
                            comp_name, missed_beats_before_dead,
                            time_to_wait_s, poll_timeout_ms):
    """Ensure a crashing component is restarted in the test lifetime."""
    kwargs['time_to_crash_s'] = 2 * beat_period_s
    kwargs['class'] = ('tests.components.test_afspm_components_monitor.'
                       + 'CrashingComponent')
    components_params_dict = {comp_name: kwargs}
    monitor = AfspmComponentsMonitor(components_params_dict,
                                     poll_timeout_ms,
                                     loop_sleep_s,
                                     missed_beats_before_dead,
                                     ctx)
    monitor._startup_processes_and_listeners()

    assert len(monitor.component_processes) == 1
    assert comp_name in monitor.component_processes
    original_pid = monitor.component_processes[comp_name].pid

    start_ts = time.time()
    monitor_and_wait(monitor, start_ts, time_to_wait_s, loop_sleep_s)

    assert len(monitor.component_processes) == 1
    assert comp_name in monitor.component_processes
    assert original_pid != monitor.component_processes[comp_name].pid


def test_exiting_component(ctx, kwargs, loop_sleep_s, beat_period_s,
                           comp_name, missed_beats_before_dead,
                           time_to_wait_s, poll_timeout_ms):
    """Ensure a purposefully exiting component is *not* restarted."""
    kwargs['time_to_exit_s'] = 2 * beat_period_s

    kwargs['class'] = ('tests.components.test_afspm_components_monitor.'
                       + 'ExitingComponent')
    components_params_dict = {comp_name: kwargs}
    monitor = AfspmComponentsMonitor(components_params_dict,
                                     poll_timeout_ms,
                                     loop_sleep_s,
                                     missed_beats_before_dead,
                                     ctx)
    monitor._startup_processes_and_listeners()

    assert len(monitor.component_processes) == 1
    assert comp_name in monitor.component_processes

    start_ts = time.time()
    monitor_and_wait(monitor, start_ts, time_to_wait_s, loop_sleep_s)

    assert len(monitor.component_processes) == 0
    assert comp_name not in monitor.component_processes
    assert comp_name not in monitor.listeners
