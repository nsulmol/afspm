"""Contains heartbeating logic (to check for frozen/crashed components)."""

import time
import tempfile
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
        _publisher: zmq PUB socket, used to send our heartbeats.
        _beat_period_s: how frequently we should send a heartbeat.
        _last_beat_ts: a timestamp of the last time we sent a
            heartbeat.
        _uuid: a uuid to differentiate beaters in logs.
    """

    def __init__(self, url: str,
                 beat_period_s: int = common.HEARTBEAT_PERIOD_S,
                 ctx: zmq.Context = None, uuid: str = None,
                 **kwargs):
        """Init heartbeater.

        Args:
            url: address we will bind to, to send hearbeats.
            beat_period_s: how frequently we should send a hearbeat.
            ctx: zmq context.
            uuid: uuid, to be used to differentiate in logs.
            kwargs: allows non-used input arguments to be passed (so we can
                initialize from an unfiltered dict).
        """
        if not ctx:
            ctx = zmq.Context.instance()

        self._publisher = ctx.socket(zmq.PUB)
        self._publisher.bind(url)
        self._beat_period_s = beat_period_s
        self._uuid = uuid

        self.last_beat_ts = time.time()

        common.sleep_on_socket_startup()

        # Send a startup beat, to indicate we have initialized.
        self._publisher.send(HBMessage.HEARTBEAT.value.to_bytes(1, 'big'))

    def handle_beat(self):
        """Send a beat if sufficient time has elapsed."""
        curr_ts = time.time()

        if curr_ts - self.last_beat_ts >= self._beat_period_s:
            self._publisher.send(HBMessage.HEARTBEAT.value.to_bytes(1, 'big'))
            self._last_beat_ts = curr_ts

    def handle_closing(self):
        """Inform any listeners that we are closing."""
        self._publisher.send(HBMessage.KILL.value.to_bytes(1, 'big'))

    def set_uuid(self, uuid: str):
        """Set id, to differentiate when logging."""
        self._uuid = uuid


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
        received_kill_signal: whether we received a KILL signal from the
            Heartbeater (implying they died on purpose).
        received_first_beat: whether we received the first heartbeat.
        _subscriber: zmq SUB socket, used to listen for heartbeats.
        _time_before_dead_s: how long we will allow before we consider the
            Heartbeater dead.
        _last_beat_ts: the timestamp of the last beat.
        _poll_timeout_ms: the poll timeout, in milliseconds.
        _uuid: a uuid to differentiate listeners in logs.
    """

    def __init__(self, url: str, beat_period_s: int = common.HEARTBEAT_PERIOD_S,
                 missed_beats_before_dead: int = common.BEATS_BEFORE_DEAD,
                 poll_timeout_ms: int = common.POLL_TIMEOUT_MS,
                 ctx: zmq.Context = None,
                 uuid: str = None, **kwargs):
        """Init listener.

        Args:
            url: address we will listen for heartbeats on.
            beat_period_s: how frequently we expect to receive a heartbeat.
            missed_beats_before_dead: how many missed beats we will allow
                before we consider the Heartbeater dead.
            poll_timeout_ms: the poll timeout, in milliseconds.
            ctx: zmq.Context.
            uuid: uuid, to be used to differentiate in logs.
            kwargs: allows non-used input arguments to be passed (so we can
                initialize from an unfiltered dict).
        """
        if not ctx:
            ctx = zmq.Context.instance()

        self._subscriber = ctx.socket(zmq.SUB)
        self._subscriber.connect(url)
        self._subscriber.setsockopt(zmq.SUBSCRIBE, b"")  # Subscribe to all

        self._time_before_dead_s = missed_beats_before_dead * beat_period_s
        self._poll_timeout_ms = poll_timeout_ms
        self._last_beat_ts = time.time()
        self._uuid = uuid

        self.received_kill_signal = False
        self.received_first_beat = False

        common.sleep_on_socket_startup()

    def check_is_alive(self) -> bool:
        """Check if the Hearbeater is alive.

        If self.time_before_dead_ms has already been met, we do not even poll.
        If not, we poll and check for a heartbeat or KILL signal.

        Returns:
            whether or not the Hearbeater is dead.
        """
        curr_ts = time.time()
        if self._subscriber.poll(self._poll_timeout_ms, zmq.POLLIN):
            # There are messages! We will keep polling until we get
            # all messages in the queue. Then we will make actions
            # based on it.
            messages = []
            while self._subscriber.poll(0, zmq.POLLIN):
                messages.append(self._subscriber.recv(zmq.NOBLOCK))
            messages = [HBMessage(int.from_bytes(msg, 'big'))
                        for msg in messages]

            if HBMessage.HEARTBEAT in messages:
                self.received_first_beat = True
                self._last_beat_ts = curr_ts
            if HBMessage.KILL in messages:
                self.received_kill_signal = True
                logger.debug(f"{self._uuid}: Listener received kill signal!")

        if (curr_ts - self._last_beat_ts >= self._time_before_dead_s or
                self.received_kill_signal):
            return False
        return True

    def reset(self):
        """Reset internal logic following a restart of Heartbeater."""
        self._last_beat_ts = time.time()
        self.received_kill_signal = False

    def set_uuid(self, uuid: str):
        """Set id, to differentiate when logging."""
        self._uuid = uuid


def get_heartbeat_url(name: str):
    """Create a hearbeat url, given a component name."""
    return "ipc://" + tempfile.gettempdir() + '/' + name
