"""Holds command ids and responses."""
import logging
from enum import Enum
from types import MappingProxyType  # Immutable dict

from google.protobuf.message import Message

from ..protos.generated import scan_pb2
from ..protos.generated import control_pb2
from ..protos.generated import feedback_pb2


logger = logging.getLogger(__name__)


# Mapping from request to proto/enum passed with it (if applicable).
# Only requests with objects linked need to be listed here.
REQ_TO_OBJ_MAP = MappingProxyType({
    control_pb2.ControlRequest.REQ_SET_SCAN_PARAMS: scan_pb2.ScanParameters2d(),
    control_pb2.ControlRequest.REQ_SET_ZCTRL_PARAMS: feedback_pb2.ZCtrlParameters(),
    control_pb2.ControlRequest.REQ_REQUEST_CTRL:
        control_pb2.ControlMode.CM_UNDEFINED,
    control_pb2.ControlRequest.REQ_ADD_EXP_PRBLM:
        control_pb2.ExperimentProblem.EP_NONE,
    control_pb2.ControlRequest.REQ_RMV_EXP_PRBLM:
        control_pb2.ExperimentProblem.EP_NONE,
    control_pb2.ControlRequest.REQ_SET_CONTROL_MODE:
        control_pb2.ControlMode.CM_UNDEFINED,
    control_pb2.ControlRequest.REQ_PARAM: control_pb2.ParameterMsg()
})

# Mapping from request to proto/enum *returned* from it (if applicable).
# Only replies eith objects linked need to be listed here.
REQ_TO_RETURN_OBJ_MAP = MappingProxyType({
    control_pb2.ControlRequest.REQ_PARAM: control_pb2.ParameterMsg()
})


def parse_request(msg: list[list[bytes]]) -> (control_pb2.ControlRequest,
                                              Message | int):
    """Helper to extract the request (and optional proto/enum) from a message.

    Args:
        msg: the bytes list corresponding to the message received.

    Returns:
        - the ControlRequest of the request
        - the associated proto or enum int, if applicable
    """
    req = int.from_bytes(msg[0], 'big')
    obj = REQ_TO_OBJ_MAP[req] if req in REQ_TO_OBJ_MAP else None
    if obj is not None:
        if isinstance(obj, Message):
            obj.ParseFromString(msg[1])
        else:
            obj = int.from_bytes(msg[1], 'big')
    return (req, obj)


def serialize_request(req: control_pb2.ControlRequest,
                      obj: Message | int = None) -> list[list[bytes]]:
    """Helper to convert a request and its additional object to bytes.

    Args:
        req: desired control request
        obj: a protobuf message or enum int (optional)

    Returns;
        a bytes array of the object after conversion.
    """

    msg = []
    msg.append(req.to_bytes(1, 'big'))
    if isinstance(obj, Message):
        msg.append(obj.SerializeToString())
    elif isinstance(obj, int):
        msg.append(obj.to_bytes(1, 'big'))
    return msg


def parse_response(req: control_pb2.ControlRequest,
                   msg: list[list[bytes]]) -> (control_pb2.ControlResponse,
                                               Message | int | None):
    """Helper to convert a response from bytes to our enum (and optional proto).

    Args:
        req: request associated to this response.
        msg: bytes list of the received response.

    Returns:
        - a ControlResponse enum instance
        - the associated proto or enum int, if applicable
    """
    rep = int.from_bytes(msg[0], 'big')
    obj = REQ_TO_RETURN_OBJ_MAP[req] if req in REQ_TO_RETURN_OBJ_MAP else None
    if obj is not None:
        if isinstance(obj, Message):
            obj.ParseFromString(msg[1])
        else:
            obj = int.from_bytes(msg[1], 'big')
    return (rep, obj)


def serialize_response(rep: control_pb2.ControlResponse,
                       obj: Message | int = None) -> list[list[bytes]]:
    """Helper to convert a response  and optional object to bytes.

    Args:
        rep: control response to convert.
        obj: a protobuf message or enum int (optional)

    Returns:
        a bytes array of the response after conversion.
    """
    msg = []
    msg.append(rep.to_bytes(1, 'big'))
    if isinstance(obj, Message):
        msg.append(obj.SerializeToString())
    elif isinstance(obj, int):
        msg.append(obj.to_bytes(1, 'big'))
    return msg
