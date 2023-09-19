"""Test the heartbeat logic."""

import threading
from enum import Enum
import time
import pytest
import zmq

from afspm.io.heartbeat.heartbeat import Heartbeater, HeartbeatListener


# ----- Fixtures ----- #
@pytest.fixture(scope="module")
def ctx():
    return zmq.Context.instance()


@pytest.fixture(scope="module")
def comm_url():
    return "tcp://127.0.0.1:6668"


@pytest.fixture(scope="module")
def comm_pub(ctx, comm_url):
    comm_publisher = ctx.socket(zmq.PUB)
    comm_publisher.bind(comm_url)
    return comm_publisher


@pytest.fixture
def comm_timeout_ms():
    return 500


@pytest.fixture
def hb_url():
    return "inproc://hb"


@pytest.fixture
def beat_period_s():
    return 0.1


@pytest.fixture
def hb_listener_timeout_ms(beat_period_s, comm_timeout_ms):
    return comm_timeout_ms + 2 * (beat_period_s * 1000)


@pytest.fixture
def missed_beats_before_dead():
    return 2

@pytest.fixture
def hb_count_flushing():
    return 2

@pytest.fixture
def hb_count_non_flushing():
    return 5


@pytest.fixture
def hb_listener(ctx, hb_url, beat_period_s, missed_beats_before_dead):
    return HeartbeatListener(hb_url, beat_period_s,
                             missed_beats_before_dead, ctx)


@pytest.fixture
def thread_hb(ctx, hb_url, beat_period_s, comm_url, comm_timeout_ms):
    thread_hb = threading.Thread(target=heartbeat_routine,
                                 args=(ctx, hb_url, beat_period_s,
                                       comm_url, comm_timeout_ms))
    thread_hb.daemon = True
    thread_hb.start()
    return thread_hb


# ----- Routines and routine communication ----- #
class CommMessage(Enum):
    """Helper to instruct Heartbeater."""
    FREEZE = 0  # Stop sending heartbeats!
    CRASH = 1  # Simply end/crash :(
    END = 2  # End in an expected fashion (with KILL signal)


def send_comm_msg(socket: zmq.Socket,
                  msg: CommMessage):
    """Send comm message."""
    socket.send(msg.value.to_bytes(1, 'big'))


def get_comm_msg(socket: zmq.Socket,
                 timeout_ms: int) -> CommMessage:
    """Get comm message and parse."""
    if socket.poll(timeout_ms, zmq.POLLIN):
        msg = socket.recv(zmq.NOBLOCK)
        return CommMessage(int.from_bytes(msg, 'big'))
    return None


def heartbeat_routine(ctx, hb_url, beat_period_s, comm_url,
                      comm_timeout_ms):
    # Set up comm
    comm = ctx.socket(zmq.SUB)
    comm.connect(comm_url)
    comm.setsockopt(zmq.SUBSCRIBE, b'')

    heartbeat = Heartbeater(hb_url, beat_period_s, ctx)

    send_heartbeats = True
    while True:
        if send_heartbeats:
            heartbeat.handle_beat()
        comm_msg = get_comm_msg(comm, comm_timeout_ms)
        if comm_msg is not None:
            if comm_msg == CommMessage.FREEZE:
                send_heartbeats = False
            if comm_msg == CommMessage.END:
                heartbeat.handle_closing()
            if comm_msg in [CommMessage.CRASH, CommMessage.END]:
                break

def check_hb_n_times(hb_listener: HeartbeatListener, hb_count: int,
                     hb_listener_timeout_ms: int,
                     assert_check: bool):
    counter = 0
    while counter < hb_count:
        if assert_check:
            assert hb_listener.check_is_alive(hb_listener_timeout_ms)
        else:
            hb_listener.check_is_alive(hb_listener_timeout_ms)
        counter += 1


# ----- Tests ----- #
def test_heartbeat_works(ctx, hb_listener, thread_hb, beat_period_s,
                         hb_listener_timeout_ms, comm_pub,
                         hb_count_non_flushing):
    """Make sure we get heartbeats for 5 heartbeats-worth of time."""
    check_hb_n_times(hb_listener, hb_count_non_flushing,
                     hb_listener_timeout_ms, True)
    send_comm_msg(comm_pub, CommMessage.END)  # Tell Heartbeat to end
    thread_hb.join()


def test_heartbeat_under_freeze(ctx, hb_listener, thread_hb, beat_period_s,
                                hb_listener_timeout_ms, comm_pub,
                                missed_beats_before_dead, hb_count_flushing):
    """Make sure we can detect the heartbeater freezing.

    We:
    - Simulate the Heartbeater freezing (comm_pub).
    - Wait a bit longer than what we have told HeartbeatListener constitutes
        a crash.
    - Check for a crash, and confirm it *is* intentional
    """
    send_comm_msg(comm_pub, CommMessage.FREEZE)

    # We cannot guarantee how many heartbeats have been sent since
    # our command. We will throw out a number of checks before validating.
    check_hb_n_times(hb_listener, hb_count_flushing,
                     hb_listener_timeout_ms, False)

    assert not hb_listener.check_is_alive(hb_listener_timeout_ms)
    assert not hb_listener.received_kill_signal

    send_comm_msg(comm_pub, CommMessage.END)  # Tell Heartbeat to end
    thread_hb.join()


def test_heartbeat_under_crash(ctx, hb_listener, thread_hb, beat_period_s,
                               hb_listener_timeout_ms, comm_pub,
                               missed_beats_before_dead, hb_count_flushing):
    """Make sure we can detect a crash.

    We:
    - Confirm we get some heartbeats.
    - Simulate the Heartbeater crashing (comm_pub).
    - Wait a bit longer than what we have told HeartbeatListener constitutes
        a crash.
    - Check for a crash, and confirm it is not intentional
    """
    send_comm_msg(comm_pub, CommMessage.CRASH)

    # We cannot guarantee how many heartbeats have been sent since
    # our command. We will throw out a number of checks before validating.
    check_hb_n_times(hb_listener, hb_count_flushing,
                     hb_listener_timeout_ms, False)

    assert not hb_listener.check_is_alive(hb_listener_timeout_ms)
    assert not hb_listener.received_kill_signal
    thread_hb.join()


def test_heartbeat_under_end(ctx, hb_listener, thread_hb, beat_period_s,
                             hb_listener_timeout_ms, comm_pub,
                             missed_beats_before_dead, hb_count_flushing):
    """Make sure we can detect a purposeful end.

    We:
    - Confirm we get some heartbeats.
    - Tell the Heartbeater to close gracefully (HBMessage.KILL).
    - Wait a bit longer than what we have told HeartbeatListener constitutes
        a crash.
    - Check for a crash, and confirm it *is* intentional
    """
    send_comm_msg(comm_pub, CommMessage.END)

    # We cannot guarantee how many heartbeats have been sent since
    # our command. We will throw out a number of checks before validating.
    check_hb_n_times(hb_listener, hb_count_flushing,
                     hb_listener_timeout_ms, False)

    assert not hb_listener.check_is_alive(hb_listener_timeout_ms)
    assert hb_listener.received_kill_signal
    thread_hb.join()
