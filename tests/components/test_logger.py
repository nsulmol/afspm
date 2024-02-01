"""Test AfspmLogger logic."""

import pytest
import logging
import zmq
from zmq.log.handlers import PUBHandler

#from afspm.components.monitor import AfspmComponentsMonitor, SPAWN_DELAY_S
from afspm.components.logger import create_local_logger_dict, AfspmLogger


logger = logging.getLogger(__name__)


@pytest.fixture
def logger_name():
    return 'logger'


@pytest.fixture
def poll_timeout_ms():
    return 250


@pytest.fixture
def local_logger_dict(logger_name, poll_timeout_ms):
    logger_dict = create_local_logger_dict()
    logger_dict['name'] = logger_name
    logger_dict.pop('class', None)  # Remove 'class', as this is for the parser
    logger_dict['poll_timeout_ms'] = poll_timeout_ms
    return logger_dict


# @pytest.fixture
# def log_urls(local_logger_dict):
#     return get_logger_params(local_logger_dict)


@pytest.fixture
def ctx():
    return zmq.Context.instance()


@pytest.fixture
def listener(local_logger_dict, ctx):
    sub = ctx.socket(zmq.SUB)
    sub.connect(local_logger_dict['pub_url'])
    sub.setsockopt(zmq.SUBSCRIBE, ''.encode())
    return sub


@pytest.fixture
def afspm_logger(local_logger_dict):
    return AfspmLogger(**local_logger_dict)


def setup_logging(url, ctx):
    pub = ctx.socket(zmq.PUB)
    pub.connect(url)
    handler = PUBHandler(pub)
    handler.setLevel(logging.DEBUG)
    logger.addHandler(handler)


def get_message(listener, poll_timeout_ms) -> str:
    if listener.poll(poll_timeout_ms, zmq.POLLIN):
        return listener.recv_multipart(zmq.NOBLOCK)
    return None


def test_logger_passthrough(local_logger_dict, listener, poll_timeout_ms,
                            afspm_logger, ctx):
    """Validate we can send logs and listen to it resent."""

    setup_logging(local_logger_dict['sub_url'], ctx)
    afspm_logger.run_per_loop()
    assert not get_message(listener, poll_timeout_ms)

    msg = "Important message!"
    logger.warning(msg)
    afspm_logger.run_per_loop()

    recvd = get_message(listener, poll_timeout_ms)
    assert 'WARN' in recvd[0].decode()
    assert msg in recvd[1].decode()
