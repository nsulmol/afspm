"""Contains heartbeating logic (to check for frozen/crashed components)."""

import time
import logging
from enum import Enum

import zmq

from .. import common


logger = logging.getLogger(__name__)


class HBMessage(Enum):
    """Different messages we can send over our heartbeat socket."""
    HEARTBEAT = 0
    KILL = 1


class Heartbeater:
    """Sends heartbeats at a set pace, when polled properly.

    Heartbeater will send 'hearbeat' messages at a set interval, provided
    its handle_heartbeat() method is called at roughly 2x the frequency it is
    expected to beat. This class, when used in conjunction with
    HeartbeatListener, can ensure we do not block an experiment due to a frozen
    or crashed component.

    Attributes:
        publisher: zmq PUB socket, used to send our heartbeats.
        beat_period_s: how frequently we should send a heartbeat.
        last_beat_ts: a timestamp of the last time we sent a
            heartbeat.
    """
    def __init__(self, url: str,
                 beat_period_s: int = common.HEARTBEAT_PERIOD_S,
                 ctx: zmq.Context = None, **kwargs):
        """Init heartbeater.

        Args:
            url: address we will bind to, to send hearbeats.
            beat_period_s: how frequently we should send a hearbeat.
            ctx: zmq context.
            kwargs: allows non-used input arguments to be passed (so we can
                initialize from an unfiltered dict).
        """
        if not ctx:
            ctx = zmq.Context.instance()

        self.publisher = ctx.socket(zmq.PUB)
        self.publisher.bind(url)
        self.beat_period_s = beat_period_s

        self.last_beat_ts = time.time()

        common.sleep_on_socket_startup()

        # Send a startup beat, to indicate we have initialized.
        self.publisher.send(HBMessage.HEARTBEAT.value.to_bytes(1, 'big'))

    def handle_beat(self):
        """Send a beat if sufficient time has elapsed."""
        curr_ts = time.time()

        if curr_ts - self.last_beat_ts >= self.beat_period_s:
            self.publisher.send(HBMessage.HEARTBEAT.value.to_bytes(1, 'big'))
            self.last_beat_ts = curr_ts

    def handle_closing(self):
        """Inform any listeners that we are closing."""
        self.publisher.send(HBMessage.KILL.value.to_bytes(1, 'big'))


class HeartbeatListener:
    """Listens for heartbeats from a listener.

    This is the counterpart to Heartbeater. It will check for heartbeats at
    the prescribed period. If we have not received missed_beats_before_dead
    beats, we presume the Heartbeater is dead and return True in check_if_dead().

    However, the Hearbeater may have *meant* to die. If so,
    self.received_kill_signal will be true.

    We can use this node to decide when we need to restart a component: if it
    appears to have died but *did not* tell us it planned to.

    Attributes:
        subscriber: zmq SUB socket, used to listen for heartbeats.
        time_before_dead_s: how long we will allow before we consider the
            Heartbeater dead.
        last_beat_ts: the timestamp of the last beat.
        received_kill_signal: whether we received a KILL signal from the
            Heartbeater (implying they died on purpose).
        poll_timeout_ms: the poll timeout, in milliseconds.
    """
    def __init__(self, url: str, beat_period_s: int = common.HEARTBEAT_PERIOD_S,
                 missed_beats_before_dead: int = common.BEATS_BEFORE_DEAD,
                 poll_timeout_ms: int = common.POLL_TIMEOUT_MS,
                 ctx: zmq.Context = None, **kwargs):
        """Init listener.

        Args:
            url: address we will listen for heartbeats on.
            beat_period_s: how frequently we expect to receive a heartbeat.
            missed_beats_before_dead: how many missed beats we will allow
                before we consider the Heartbeater dead.
            ctx: zmq.Context.
            kwargs: allows non-used input arguments to be passed (so we can
                initialize from an unfiltered dict).
            poll_timeout_ms: the poll timeout, in milliseconds.
        """
        if not ctx:
            ctx = zmq.Context.instance()

        self.subscriber = ctx.socket(zmq.SUB)
        self.subscriber.connect(url)
        self.subscriber.setsockopt(zmq.SUBSCRIBE, b"")  # Subscribe to all

        self.time_before_dead_s = missed_beats_before_dead * beat_period_s
        self.poll_timeout_ms = poll_timeout_ms
        self.last_beat_ts = time.time()
        self.received_kill_signal = False
        self.received_first_beat = False

        common.sleep_on_socket_startup()

    def check_is_alive(self) -> bool:
        """Checks if the Hearbeater is alive.

        If self.time_before_dead_ms has already been met, we do not even poll.
        If not, we poll and check for a heartbeat or KILL signal.

        Returns:
            whether or not the Hearbeater is dead.
        """
        curr_ts = time.time()
        if self.subscriber.poll(self.poll_timeout_ms, zmq.POLLIN):
            msg = self.subscriber.recv(zmq.NOBLOCK)
            msg_enum = HBMessage(int.from_bytes(msg, 'big'))
            if msg_enum == HBMessage.HEARTBEAT:
                self.received_first_beat = True
                self.last_beat_ts = curr_ts
            elif msg_enum == HBMessage.KILL:
                self.received_kill_signal = True
            else:
                logger.warning("Received non-HBMessage message. Ignoring.")

        if (curr_ts - self.last_beat_ts >= self.time_before_dead_s or
                self.received_kill_signal):
            return False
        return True

    def reset(self):
        """Reset internal logic following a restart of Heartbeater."""
        self.last_beat_ts = time.time()
        self.received_kill_signal = False
