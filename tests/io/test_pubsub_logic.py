""" Test publisher-subscriber logic."""

import logging
import threading
import time
import pytest
import zmq

from google.protobuf.message import Message

from afspm.io.cache import cache_logic as cl
from afspm.io.cache import pbc_logic as pbc
from afspm.io.pubsub import publisher
from afspm.io.pubsub import subscriber
from afspm.io.pubsub import pubsubcache
from afspm.io import common

from afspm.io.protos.generated import scan_pb2
from afspm.io.protos.generated import control_pb2


logger = logging.getLogger(__name__)


# -------------------- Fixtures -------------------- #

@pytest.fixture
def ctx():
    return zmq.Context.instance()


@pytest.fixture(scope="module")
def pub_url():
    return "tcp://127.0.0.1:5555"


@pytest.fixture(scope="module")
def psc_url():
    return "tcp://127.0.0.1:5556"


@pytest.fixture(scope="module")
def cache_kwargs():
    return {"cache_logic": pbc.ProtoBasedCacheLogic()}


@pytest.fixture
def pub(pub_url):
    return publisher.Publisher(pub_url,
                               cl.CacheLogic.get_envelope_for_proto)

@pytest.fixture(scope="module")
def topics_scan2d():
    return [cl.CacheLogic.get_envelope_for_proto(scan_pb2.Scan2d())]


@pytest.fixture(scope="module")
def topics_control_state():
    return [cl.CacheLogic.get_envelope_for_proto(control_pb2.ControlState())]


@pytest.fixture(scope="module")
def topics_both():
    return [cl.CacheLogic.get_envelope_for_proto(scan_pb2.Scan2d()),
            cl.CacheLogic.get_envelope_for_proto(control_pb2.ControlState())]


@pytest.fixture(scope="module")
def wait_ms():
    return 500


@pytest.fixture
def sub_scan_pub(ctx, pub_url, topics_scan2d, cache_kwargs,
                 wait_ms):
    return subscriber.Subscriber(
        pub_url, cl.extract_proto, topics_scan2d,
        cl.update_cache, ctx,
        extract_proto_kwargs=cache_kwargs,
        update_cache_kwargs=cache_kwargs)


@pytest.fixture
def sub_control_state_pub(ctx, pub_url, topics_control_state, cache_kwargs,
                          wait_ms):
    return subscriber.Subscriber(
        pub_url, cl.extract_proto, topics_control_state,
        cl.update_cache, ctx,
        extract_proto_kwargs=cache_kwargs,
        update_cache_kwargs=cache_kwargs)


@pytest.fixture
def sub_scan_psc(ctx, psc_url, topics_scan2d, cache_kwargs,
                 wait_ms):
    return subscriber.Subscriber(
        psc_url, cl.extract_proto, topics_scan2d,
        cl.update_cache, ctx,
        extract_proto_kwargs=cache_kwargs,
        update_cache_kwargs=cache_kwargs)


@pytest.fixture
def sub_control_state_psc(ctx, psc_url, topics_control_state, cache_kwargs,
                          wait_ms):
    return subscriber.Subscriber(
        psc_url, cl.extract_proto, topics_control_state,
        cl.update_cache, ctx,
        extract_proto_kwargs=cache_kwargs,
        update_cache_kwargs=cache_kwargs)


@pytest.fixture
def sample_scan():
    scan = scan_pb2.Scan2d()
    scan.params.name = 'john doe'
    return scan


@pytest.fixture
def control_state():
    cs = control_pb2.ControlState()
    cs.control_mode = control_pb2.ControlMode.CM_PROBLEM
    cs.problems_set.append(
        control_pb2.ExperimentProblem.EP_TIP_SHAPE_CHANGED)
    return cs


# -------------------- PubSub Tests -------------------- #

def test_pub_send_msg(ctx, pub, sample_scan):
    """Confirm we can connect and send messages into the void.

    Messages sent with no subscriber are just shelved. We should get no fail
    messages or error.
    """
    pub.send_msg(sample_scan)


def assert_sub_received_proto(sub: subscriber.Subscriber,
                              proto: Message,
                              wait_ms: int):
    """Confirm a message is received by a subscriber."""
    assert sub.poll_and_store(wait_ms)
    assert len(sub.cache[cl.CacheLogic.get_envelope_for_proto(proto)]) == 1
    assert (sub.cache[cl.CacheLogic.get_envelope_for_proto(proto)][0]
            == proto)


def test_pubsub_simple(pub_url, cache_kwargs, ctx, pub, topics_both,
                       sub_scan_pub, sub_control_state_pub, wait_ms,
                       sample_scan, control_state):
    """ Test a pub-sub network *without* our pubsubcache.

    We will test that:
    - subscribers receive only messages from the envelopes they
    subscribe.
    - a new subscriber does not receive messages from the cache (since there
    is none).
    - messages sent after a new subscriber are sent properly.
    """
    sub_scan = sub_scan_pub
    sub_control_state = sub_control_state_pub

    # Connect 2 subscribers and confirm we can send separate message envelopes.
    # (Subscribers have been registered via pytest.fixture)
    pub.send_msg(sample_scan)
    assert not sub_control_state.poll_and_store(wait_ms)
    assert_sub_received_proto(sub_scan, sample_scan, wait_ms)

    pub.send_msg(control_state)
    assert not sub_scan.poll_and_store(wait_ms)
    assert_sub_received_proto(sub_control_state, control_state, wait_ms)

    # Connect a 3rd subscriber and confirm we *do not* re-receive the old
    # messages (since we do not have a pubsubcache setup).
    sub_both = subscriber.Subscriber(
        pub_url, cl.extract_proto, topics_both,
        cl.update_cache, ctx,
        extract_proto_kwargs=cache_kwargs,
        update_cache_kwargs=cache_kwargs)

    assert not sub_scan.poll_and_store(wait_ms)
    assert not sub_control_state.poll_and_store(wait_ms)
    assert not sub_both.poll_and_store(wait_ms)

    # Send a scan again, confirm both and sub_scan receive
    pub.send_msg(sample_scan)
    assert not sub_control_state.poll_and_store(wait_ms)
    assert_sub_received_proto(sub_both, sample_scan, wait_ms)
    assert_sub_received_proto(sub_scan, sample_scan, wait_ms)


# --------------------- PubSubCache tests -------------------- #
@pytest.fixture(scope="module")
def comm_url():
    return "tcp://127.0.0.1:7777"


@pytest.fixture
def comm_pub(ctx, comm_url):
    comm_publisher = ctx.socket(zmq.PUB)
    comm_publisher.bind(comm_url)
    yield comm_publisher
    comm_publisher.close()


@pytest.fixture(scope="module")
def short_wait_ms():
    return 25

@pytest.fixture(scope="module")
def wait_count():
    return 3


def got_kill_signal(socket: zmq.Socket,
                    timeout_ms: int) -> bool:
    """See if we received a signal from the socket."""
    if socket.poll(timeout_ms, zmq.POLLIN):
        msg = socket.recv_multipart(zmq.NOBLOCK)
        envelope = msg[0].decode()
        if envelope == common.KILL_SIGNAL:
            return True
    return False


def send_kill_signal(socket: zmq.Socket):
    """Send the kill signal out on a socket."""
    socket.send_multipart([common.KILL_SIGNAL.encode()])


def kill_and_wait(socket: zmq.Socket, wait_ms: int,
                  wait_count: int, thread: threading.Thread):
    """Tell thread to die, wait, and join."""
    send_kill_signal(socket)
    time.sleep(wait_count * wait_ms / 1000)
    thread.join()


def pubsubcache_routine(psc_url, pub_url, comm_url, short_wait_ms,
                        ctx, cache_kwargs):
    """Routine to create and run a pubsubcache."""
    comm = ctx.socket(zmq.SUB)
    comm.connect(comm_url)
    comm.setsockopt(zmq.SUBSCRIBE, b'')

    psc = pubsubcache.PubSubCache(psc_url, pub_url,
                                  cl.extract_proto,
                                  cl.CacheLogic.get_envelope_for_proto,
                                  cl.update_cache, ctx,
                                  extract_proto_kwargs=cache_kwargs,
                                  update_cache_kwargs=cache_kwargs)
    stay_alive = True
    while stay_alive:
        psc.poll(short_wait_ms)
        if got_kill_signal(comm, short_wait_ms):
            logging.debug("Kill signal received, sending through PSC")
            psc.send_kill_signal()
            stay_alive = False

    logging.debug("Dying, closing sockets")
    # Close bound sockets
    psc.backend.close()

@pytest.fixture
def thread_psc(psc_url, pub_url, comm_url, short_wait_ms, ctx, wait_ms,
               cache_kwargs):
    thread = threading.Thread(target=pubsubcache_routine,
                              args=(psc_url, pub_url, comm_url, short_wait_ms,
                                    ctx, cache_kwargs))
    thread.daemon = True
    thread.start()
    return thread


@pytest.fixture
def sub_all_topics_psc(psc_url, cache_kwargs, ctx, wait_ms):
    all_topics = [common.ALL_ENVELOPE]
    return subscriber.Subscriber(
        psc_url, cl.extract_proto, all_topics,
        cl.update_cache, ctx,
        extract_proto_kwargs=cache_kwargs,
        update_cache_kwargs=cache_kwargs)


def test_pubsubcache_kill_signal(sub_all_topics_psc, wait_ms, wait_count,
                                 comm_pub, thread_psc):
    """Validate that a kill signal is received by a subscriber."""
    assert not sub_all_topics_psc.poll_and_store(wait_ms)
    assert not sub_all_topics_psc.was_shutdown_requested()

    kill_and_wait(comm_pub, wait_ms, wait_count, thread_psc)

    sub_all_topics_psc.poll_and_store(wait_ms)
    assert sub_all_topics_psc.was_shutdown_requested()


def test_pubsubcache(psc_url, cache_kwargs, ctx, pub, topics_both,
                     sub_scan_psc, sub_control_state_psc,
                     sample_scan, control_state, wait_ms, wait_count,
                     thread_psc, comm_pub):
    """ Test a pub-sub network *with* our pubsubcache.

    We will test that:
    - subscribers receive only messages from the envelopes they
    subscribe.
    - upon a new subscriber, old cache messages (from each newly subscribed
    envelope) are received by all current subscribers.
    - messages sent after a new subscriber are sent properly.
    """
    sub_scan = sub_scan_psc
    sub_control_state = sub_control_state_psc

    # Connect 2 subscribers and confirm we can send separate message envelopes.
    # (Subscribers have been registered via pytest.fixture)
    pub.send_msg(sample_scan)
    assert not sub_control_state.poll_and_store(wait_ms)
    assert_sub_received_proto(sub_scan, sample_scan, wait_ms)

    pub.send_msg(control_state)
    assert not sub_scan.poll_and_store(wait_ms)
    assert_sub_received_proto(sub_control_state, control_state, wait_ms)

    # Connect a 3rd subscriber and confirm we *do* re-receive the old
    # messages (since we have a pubsubcache setup).
    sub_both = subscriber.Subscriber(
        psc_url, cl.extract_proto, topics_both,
        cl.update_cache, ctx,
        extract_proto_kwargs=cache_kwargs,
        update_cache_kwargs=cache_kwargs)

    assert sub_scan.poll_and_store(wait_ms)
    assert sub_control_state.poll_and_store(wait_ms)
    # Since we have subscribed to 2 topics (and both have messages in the
    # cache), we validate that we receive data 2x.
    assert sub_both.poll_and_store(wait_ms)
    assert sub_both.poll_and_store(wait_ms)

    # Send a scan again, confirm both and sub_scan receive
    pub.send_msg(sample_scan)
    assert not sub_control_state.poll_and_store(wait_ms)
    assert_sub_received_proto(sub_both, sample_scan, wait_ms)
    assert_sub_received_proto(sub_scan, sample_scan, wait_ms)

    kill_and_wait(comm_pub, wait_ms, wait_count, thread_psc)
