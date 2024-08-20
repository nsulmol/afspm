"""Holds methods to communicate with Asylum zmq-xop interface.

Note that we focus here on the cases we will use to control the asylum
microscope. There are other call structures that can be done, which will not
be covered by these methods.
"""

from enum import Enum
import json
import logging
from typing import Optional

# TODO: Add more imports!

logger = logging.getLogger(__name__)


IGOR_SEP = ':'
PY_SEP = '/'


# TODO: Do we store the below in some other format? Data class? something
# to explicit what are const vs. global varaible (really, just msg counter...)

# Igor JSON format variables
VER_KEY = 'version'
VER_VAL = 1
MSG_ID_KEY = 'messageID'
MSG_COUNTER = 0  # We hold the message id counter
MSG_COUNTER_OVERFLOW = 500  # Overflow point. I.e. we modulus with this.
CALL_KEY = 'CallFunction'
CALL_NAME_KEY = 'name'
CALL_PARAMS_KEY = 'params'

ERROR_KEY = 'errorCode'
MSG_KEY = 'msg'
VAL_KEY = 'value'
TYPE_KEY = 'type'
RES_KEY = 'result'


class XOPSyntaxError(RuntimeError):
    """Provided input does not meet our expected message syntax."""


class XOPUnsupportedTypeError(TypeError):
    """Provided IgorType that is not supported."""


class IgorType(str, Enum):
    """String defines the igor data type."""

    VARIABLE = 'variable'
    STRING = 'string'
    WAVE = 'wave'


def _create_message_id() -> str:
    """Create a message id and update message counter."""
    global MSG_COUNTER
    message_id = str(MSG_COUNTER)
    MSG_COUNTER = (MSG_COUNTER + 1) % MSG_COUNTER_OVERFLOW
    return message_id


def convert_igor_path_to_python_path(igor_path: str) -> str:
    """Converts a path received from Igor into python format.

    Igor stores file paths with back- or forward- slashes all replaced
    by colons (e.g. "C:Users:nsulmol:Pictures" instead of
    "C:/Users/nsulmol/Pictures"). This method will convert from igor
    to python format.

    Args:
        igor_path: path in igor format, as a string.

    Returns:
        path in python format, as a string.
    """
    python_path = igor_path.replace(IGOR_SEP, PY_SEP)
    first_idx = python_path.find(PY_SEP)

    # Add slash to first colon (e.g. 'C:'), as we need a sep (e.g. 'C:/')
    python_path = (python_path[:first_idx] + IGOR_SEP +
                   python_path[first_idx:])
    return python_path


def convert_python_path_to_igor_path(python_path: str) -> str:
    """Convert a path in python format to Igor format.

    See _convert_igor_path_to_python_path for more info.
    """
    igor_path = python_path.replace(PY_SEP, IGOR_SEP)
    first_idx = igor_path.find(IGOR_SEP + IGOR_SEP)  # Should have '::i

    if first_idx != -1:
        # Remove second ':'
        igor_path = igor_path[:first_idx] + igor_path[first_idx+1:]

    return igor_path


def create_call_string(method_name: str,
                       params: Optional[tuple[float | str]] = None
                       ) -> (str, str):
    """Create a JSON 'call string' for the provided method and params.

    Args:
        method_name: name of method to call, as a string.
        params: parameters to send, if applicable.

    Returns:
        (message_id, json_encoded_str), where:
        - message_id is the id we have provided to this message. Used to
        ensure we grab the proper response (which will also have this id).
        - json_encoded_str is the JSON encoded string of the call, in the
        format applicable for the zmq-xop interface.
    """
    message_id = _create_message_id()
    structure = {VER_KEY: VER_VAL,
                 MSG_ID_KEY: message_id}

    method_dict = {CALL_NAME_KEY: method_name}
    if params:
        method_dict[CALL_PARAMS_KEY] = list(params)
    structure[CALL_KEY] = method_dict

    return message_id, json.dumps(structure)


def parse_response_string(response: str) -> (int, int, Optional[float | str]):
    """Parse a JSON response into error code and return data.

    Args:
        response: a response string, in zmq-xop interface JSON format.

    Returns:
        (error_code, message_id, return_val) tuple, where:
        - error_code is the returned error code, as an int.
        - message_id matches the call it is responding to.
        - the return value (if applicable).
    """
    structure = json.loads(response)

    if ERROR_KEY not in structure or MSG_ID_KEY not in structure:
        logger.error("ZMQ-XOP Response Received does not make sense!")
        raise XOPSyntaxError

    error = structure[ERROR_KEY][VAL_KEY]
    message_id = structure[MSG_ID_KEY]

    if error != 0:
        error_msg = structure[ERROR_KEY][MSG_KEY]
        logger.error(f"Error {error} for message id {message_id}: {error_msg}")

    if RES_KEY not in structure:
        return error, error_msg, message_id, None

    res_type = IgorType(structure[RES_KEY][TYPE_KEY])
    if res_type == IgorType.WAVE:
        logger.error("ZMQ-XOP response included wave, which is not currently "
                     "supported.")
        raise XOPUnsupportedTypeError

    value = structure[RES_KEY][VAL_KEY]
    return error, message_id, value
