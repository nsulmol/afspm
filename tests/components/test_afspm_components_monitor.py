"""Test the afspm_components_monitor module logic."""

import time
import pytest
import zmq

from afspm.components.afspm_component import AfspmComponent
from afspm.components.afspm_components_monitor import AfspmComponentsMonitor


# ----- Fixtures ----- #
@pytest.fixture
def comp_name():
    return 'test_component'


@pytest.fixture
def loop_sleep_s():
    return 0.05


@pytest.fixture
def hb_period_s():
    return 0.1


@pytest.fixture
def kwargs(comp_name, loop_sleep_s, hb_period_s):
    kwargs_dict = {}
    kwargs_dict['name'] = comp_name
    kwargs_dict['loop_sleep_s'] = loop_sleep_s
    kwargs_dict['hb_period_s'] = hb_period_s
    return kwargs_dict


@pytest.fixture
def missed_beats_before_dead():
    return 5


@pytest.fixture
def ctx():
    return zmq.Context()


@pytest.fixture
def time_to_wait_s(hb_period_s, missed_beats_before_dead):
    return 2 * hb_period_s * missed_beats_before_dead


# ----- Classes for Testing ----- #
class CrashingComponent(AfspmComponent):
    """A simple component that crashes after some time."""
    def __init__(self, time_to_crash_s: float, **kwargs):
        self.time_to_crash_s = time_to_crash_s
        self.start_ts = time.time()
        super().__init__(**kwargs)

    def run_per_loop(self):
        curr_ts = time.time()
        if curr_ts - self.start_ts >= self.time_to_crash_s:
            raise SystemExit


class ExitingComponent(AfspmComponent):
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
def test_basic_component(ctx, kwargs, loop_sleep_s, hb_period_s,
                         comp_name, missed_beats_before_dead,
                         time_to_wait_s):
    """Ensure a standard component stays alive for the test lifetime."""
    kwargs['class'] = 'afspm.components.afspm_component.AfspmComponent'
    components_params_dict = {comp_name: kwargs}
    monitor = AfspmComponentsMonitor(components_params_dict,
                                     loop_sleep_s,
                                     missed_beats_before_dead,
                                     ctx)
    assert len(monitor.component_processes) == 1
    assert comp_name in monitor.component_processes
    original_pid = monitor.component_processes[comp_name].pid

    start_ts = time.time()
    monitor_and_wait(monitor, start_ts, time_to_wait_s, loop_sleep_s)

    assert len(monitor.component_processes) == 1
    assert comp_name in monitor.component_processes
    assert original_pid == monitor.component_processes[comp_name].pid


def test_crashing_component(ctx, kwargs, loop_sleep_s, hb_period_s,
                            comp_name, missed_beats_before_dead,
                            time_to_wait_s):
    """Ensure a crashing component is restarted in the test lifetime."""
    kwargs['time_to_crash_s'] = 2 * hb_period_s
    kwargs['class'] = ('tests.components.test_afspm_components_monitor.'
                       + 'CrashingComponent')
    components_params_dict = {comp_name: kwargs}
    monitor = AfspmComponentsMonitor(components_params_dict,
                                     loop_sleep_s,
                                     missed_beats_before_dead,
                                     ctx)
    assert len(monitor.component_processes) == 1
    assert comp_name in monitor.component_processes
    original_pid = monitor.component_processes[comp_name].pid

    start_ts = time.time()
    monitor_and_wait(monitor, start_ts, time_to_wait_s, loop_sleep_s)

    assert len(monitor.component_processes) == 1
    assert comp_name in monitor.component_processes
    assert original_pid != monitor.component_processes[comp_name].pid


def test_exiting_component(ctx, kwargs, loop_sleep_s, hb_period_s,
                           comp_name, missed_beats_before_dead,
                           time_to_wait_s):
    """Ensure a purposefully exiting component is *not* restarted."""
    kwargs['time_to_exit_s'] = 2 * hb_period_s

    kwargs['class'] = ('tests.components.test_afspm_components_monitor.'
                       + 'ExitingComponent')
    components_params_dict = {comp_name: kwargs}
    monitor = AfspmComponentsMonitor(components_params_dict,
                                     loop_sleep_s,
                                     missed_beats_before_dead,
                                     ctx)
    assert len(monitor.component_processes) == 1
    assert comp_name in monitor.component_processes

    start_ts = time.time()
    monitor_and_wait(monitor, start_ts, time_to_wait_s, loop_sleep_s)

    assert len(monitor.component_processes) == 0
    assert comp_name not in monitor.component_processes
    assert comp_name not in monitor.listeners
