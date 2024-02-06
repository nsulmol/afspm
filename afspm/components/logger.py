"""Holds the logger component, useful for experiments run on multiple PCs."""

import logging
import zmq
import zmq.log.handlers

from ..io import common
from ..io.heartbeat import heartbeat
from .component import AfspmComponentBase


logger = logging.getLogger(__name__)


class AfspmLogger(AfspmComponentBase):
    """Main logging component, to receive all logs and store in one place.

    This component involves 2 zmq sockets:
    - An 'input' bound SUB socket, which receives all of the published logs
    from the multiple publishers; and
    - An 'output' bound PUB socket, which re-publishes all messages received
    in a single socket.

    Each PC running 1+ AfspmComponents publishes their logs to the input
    socket via a zmq.log.PUBHandler. All of these are received by this logger
    and output to a single 'stream' (the output socket). Additionally, this
    logger will optionally save this stream to a provided file.

    IMPORTANT: only 1 device should be running this logger component during an
    experiment (presumably, the one that is able to bind the input socket url).
    If another device tries to run it, we expect failures/errors.

    Notes:
    - The logger does not currently *filter* different log levels. It is
    assumed that each device has filtered their log levels on their end, on
    startup.
    - If you want to listen in to an AfspmLogger's PUB socket without adding a
    new component, due it anywhere via:
        python -m zmq.log $ZMQ_URL
    where $ZMQ_URL is the pub_url  of your logger. This requires zmq to be
    in your python installation.

    Attributes:
        ctx: ZMQ context, so we can force-close everything when ending.
        sub: bound socket where we receive all logs.
        pub: bound socket where we publish the merged log.
        filepath: the filepath where we save the output log, if not None.
        poll_timeout_ms: how long to wait per poll.
    """

    def __init__(self, sub_url: str, pub_url: str = None, filepath: str = None,
                 poll_timeout_ms: int = common.POLL_TIMEOUT_MS,
                 ctx: zmq.Context = None, **kwargs):
        """Initialize logger.

        Args:
            sub_url: input url, where we recieve all logs. Required.
            pub_url: output url, where we published merged log. Default None,
                meaning we do not publish our results.
            filepath: where we save the output log. Default None.
            poll_timeout_ms: how long to wait per poll.
            ctx: zmq context to use. Default None.
        """
        logger.debug("Initializing logger.")
        if not ctx:
            ctx = zmq.Context.instance()
        self.ctx = ctx

        self.sub = ctx.socket(zmq.SUB)
        self.sub.bind(sub_url)
        # Subscribe to all topics
        self.sub.setsockopt(zmq.SUBSCRIBE, ''.encode())

        self.pub = None
        if pub_url:
            self.pub = ctx.socket(zmq.PUB)
            self.pub.bind(pub_url)

        self.filepath = filepath
        self.poll_timeout_ms = poll_timeout_ms

        kwargs['ctx'] = ctx
        super().__init__(**kwargs)

    def run_per_loop(self):
        """Override to receive logs and write to file/pub (as applicable)."""
        if self.sub.poll(self.poll_timeout_ms, zmq.POLLIN):
            topic, msg = self.sub.recv_multipart(zmq.NOBLOCK)

            if self.filepath:
                with open(self.filepath, 'w') as writer:
                    writer.write(msg.decode() + '\n')

            if self.pub:
                self.pub.send_multipart([topic, msg])


def get_url_for_logging(logger_dict: dict) -> str:
    """Extract the url we should be logging to from AfspmLogger dict."""
    if 'sub_url' in logger_dict:
        return logger_dict['sub_url']
    return None


def create_local_logger_dict() -> dict:
    """Create a default local AfspmLogger params, if none exists.

    Note: this is currently only used in the UTs, but could be considered
    if we switch to having all 'spawn' calls hook into the output of
    the AfspmLogger for its logging. This doesn't seem necessary now,
    and it simply adds complexity...
    """
    params_dict = {}
    params_dict['class'] = 'afspm.components.logger.AfspmLogger'
    params_dict['sub_url'] = heartbeat.get_heartbeat_url('LocalLoggerSub')
    params_dict['pub_url'] = heartbeat.get_heartbeat_url('LocalLoggerPub')
    params_dict['filepath'] = heartbeat.get_heartbeat_url('log.txt').split(
        'ipc://')[1]

    logger.warning("No logger was provided in config. Creating a default "
                   "with: %s, using default extract_proto and update_cache.",
                   params_dict)
    return params_dict
