"""Holds zmq-xop client logic."""

import logging
import time
import zmq
from typing import Optional

from afspm.io.common import POLL_TIMEOUT_MS
from afspm.components.microscope.translators.asylum import xop


logger = logging.getLogger(__name__)


DEFAULT_XOP_URL = 'tcp://127.0.0.1:5555'

# Asylum takes a while to respond at times.
DEFAULT_TIMEOUT_S = 5


class XopMessageError(Exception):
    """The parsed response indicates a generic message error.

    This is a duplicate of that in xop. I am catching that error and
    funneling it up into an error at this level. This is questionable in
    terms of coding quality: my goal was to encapsulate knowledge into xop
    (the user of client should not need to import xop).
    """


class XopClient:
    """Hold zmq-xop client logic.

    The XopClient will create a zmq connection with the asylum controller via
    a zmq interface. Afterward, any desired requests can be sent and responses
    parsed via send_request.

    Attributes:
        _url: address of server we are connecting to.
        _timeout_s: how long to wait before concluding a sent request has not
            been responded to. Defaults to DEFAULT_TIMEOUT_S.
        _client: zmq socket used to connect to server.
    """

    def __init__(self, url: str = DEFAULT_XOP_URL,
                 timeout_s: int = DEFAULT_TIMEOUT_S,
                 ctx: zmq.Context = None):
        """Init constructor."""
        if not ctx:
            ctx = zmq.Context.instance()
        self._url = url
        self._timeout_s = timeout_s

        self._client = ctx.socket(zmq.REQ)
        self._client.connect(self._url)

    def send_request(self, method_name: str,
                     params: Optional[tuple[float | str]] = None,
                     ) -> (bool, float | str):
        """Send asylum request.

        Given a method name and tuple of parameters, send a request to call
        this method to asylum. The format of the call is:
            method_name(params[0], params[1], ...)

        Note that we only support a single return value with this method,
        even though the xop supports multiple. (We don't currently make any
        multiple-return-value calls).

        Args:
            method_name: method name, as str.
            params: tuple of parameters to feed the method. Optional. Default
                is None. This could consist of, for example:
                - (attrib), for something like GetValue(attrib)
                - (attrib, val), for something like SetValue(attrib, val)

        Returns:
            (msg_received, ret_val), where
            msg_received: whether or not we received a response from this
                request.
            ret_val: the returned value, if applicable.
        """
        req_msg_id, req = xop.create_call_string(method_name, params)
        logger.trace(f'Call string to send: {req}')
        self._client.send(req.encode())
        ts = time.time()

        # Note: we use this ugly approach because the server may be responding
        # to multiple requests (with different req_msg_ids). Thus, we may
        # receive multiple messages that are not for us!
        # Note: HIGHLY unlikely, but why not.
        msg_received = False
        err_code = None
        rep_msg_id = None
        ret_val = None
        while not msg_received and time.time() - ts < self._timeout_s:
            if self._client.poll(POLL_TIMEOUT_MS, zmq.POLLIN):
                msg = self._client.recv(zmq.NOBLOCK).decode()
                logger.trace(f'Received response: {msg}')
                try:
                    err_code, rep_msg_id, ret_val = xop.parse_response_string(
                        msg)
                except (xop.XOPMessageError, xop.XOPSyntaxError,
                        xop.XOPUnsupportedTypeError) as e:
                    # Catch major exceptions and funnel up to general.
                    # (We log the specific errors, and the user of this
                    # method should not have to concern themselves with
                    # such particulars)
                    raise XopMessageError(getattr(e, 'message', repr(e)))

                msg_received = req_msg_id == rep_msg_id
        return msg_received, ret_val
