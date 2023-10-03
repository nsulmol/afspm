"""Holds command ids and responses."""
from enum import Enum
from types import MappingProxyType  # Immutable dict

from google.protobuf.message import Message

from ..protos.generated import scan_pb2
from ..protos.generated import control_pb2


# Mapping from request to proto/enum passed with it (if applicable).
REQ_TO_OBJ_MAP = MappingProxyType({
    control_pb2.ControlRequest.REQ_START_SCAN: None,
    control_pb2.ControlRequest.REQ_STOP_SCAN: None,
    control_pb2.ControlRequest.REQ_SET_SCAN_PARAMS: scan_pb2.ScanParameters2d(),
    control_pb2.ControlRequest.REQ_REQUEST_CTRL: control_pb2.ControlMode.CM_UNDEFINED,
    control_pb2.ControlRequest.REQ_RELEASE_CTRL: None,
    control_pb2.ControlRequest.REQ_ADD_EXP_PRBLM: control_pb2.ExperimentProblem.EP_NONE,
    control_pb2.ControlRequest.REQ_RMV_EXP_PRBLM: control_pb2.ExperimentProblem.EP_NONE,
    control_pb2.ControlRequest.REQ_SET_CONTROL_MODE: control_pb2.ControlMode.CM_UNDEFINED,
    control_pb2.ControlRequest.REQ_END_EXPERIMENT: None
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
    obj = REQ_TO_OBJ_MAP[req]
    if obj is not None:
        if isinstance(obj, Message):
            obj.ParseFromString(msg[1])
        else:
            obj = int.from_bytes(msg[1], 'big')
    return (req, obj)


def serialize_req_obj(req: control_pb2.ControlRequest,
                      obj: Message | int = None) -> list[list[bytes]]:
    """Helper to convert a request and its additional object to bytes.

    Args:
        req: desired control request
        obj: a protobuf message or enum int

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


def parse_response(msg: list[bytes]) -> control_pb2.ControlResponse:
    """Helper to convert a response from bytes to our enum.

    Args:
        msg: bytes array of the received response.

    Returns:
        a ControlResponse enum instance.
    """
    return int.from_bytes(msg, 'big')


def serialize_response(rep: control_pb2.ControlResponse) -> list[bytes]:
    """Helper to convert a response to bytes.

    Args:
        rep: control response to convert.

    Returns:
        a bytes array of the response after conversion.
    """
    return rep.to_bytes(1, 'big')
