"""Holds common I/O logic."""
import time

from google.protobuf.internal.enum_type_wrapper import EnumTypeWrapper

from ..io.protos.generated import scan_pb2
from ..io.protos.generated import geometry_pb2
from ..io.protos.generated import control_pb2
from ..io.protos.generated import spec_pb2


# --- Common envelope/signal stuff --- #
KILL_SIGNAL = "KILL"
ALL_ENVELOPE = ""
ALL_ENVELOPE_LOG = "ALL"


# --- Good defaults --- #
REQUEST_TIMEOUT_MS = 250  # Linked to TCP delay
POLL_TIMEOUT_MS = 25
LOOP_SLEEP_S = 0.1  # 100 ms
HEARTBEAT_PERIOD_S = 1
BEATS_BEFORE_DEAD = 3


# We appear to need a small startup delay, to allow zmq sockets to properly
# get setup.
_STARTUP_SLEEP_S = 0.25  # 250 ms


def sleep_on_socket_startup():
    """Sleep a set amount of time on spawning of a zmq socket.

    Not doing this can cause weird race conditions.
    """
    time.sleep(_STARTUP_SLEEP_S)


# --- Creation Helpers --- #
def create_scan_params_2d(top_left: tuple[float, float] = None,
                          size: tuple[float, float] = None,
                          length_units: str = None,
                          angular_units: str = None,
                          data_shape: tuple[int, int] = None,
                          data_units: str = None,
                          angle: int = 0,
                          ) -> scan_pb2.ScanParameters2d:
    """Create ScanParameters2d object.

    This is merely a helper, to avoid annoyances with the protobuf data
    initialization.

    Args:
        top_left: physical roi, top-left position.
        size: physical roi, size.
        length_units: units of the length/distance dimensions.
        angular_units: units of the rotation.
        data_shape: 2D shape of the 2D scan.
        data_units: units of the z-dimension.
        channel_name: name of the channel this data comes from.
        angle: rotation angle of physical roi. Defaults to 0.

    Returns:
        Initialized ScanParameters2d instance.
    """
    top_left = (geometry_pb2.Point2d(x=top_left[0], y=top_left[1]) if top_left
                else None)
    size = geometry_pb2.Size2d(x=size[0], y=size[1]) if size else None
    roi = geometry_pb2.RotRect2d(top_left=top_left, size=size, angle=angle)
    da_shape = (geometry_pb2.Size2u(x=data_shape[0], y=data_shape[1])
                if data_shape else None)
    spatial_aspects = scan_pb2.SpatialAspects(roi=roi,
                                              length_units=length_units,
                                              angular_units=angular_units)
    data_aspects = scan_pb2.DataAspects(shape=da_shape, units=data_units)
    return scan_pb2.ScanParameters2d(spatial=spatial_aspects,
                                     data=data_aspects)


def create_probe_pos(pos: tuple[float, float] = None,
                     units: str = None):
    """Create ProbePosition object.

    A helper, to avoid annoyances with protobuf data initialization.

    Args:
        probe_pos: desired physical  position of probe.
        units: units of the length/distance dimensions.
    """
    pos = (geometry_pb2.Point2d(x=pos[0], y=pos[1]) if pos else None)
    return spec_pb2.ProbePosition(point=pos, units=units)


def create_action_msg(action: str) -> control_pb2.ActionMsg:
    """Convert an ActionParameter enum / str to the probotuf message.

    Args:
        action: str corresponding to the action we
            wish to send.

    Returns:
        control_pb2.ActionMsg with action contained in it.
    """
    return control_pb2.ActionMsg(action=action)


def get_action_from_msg(msg: control_pb2.ActionMsg) -> str:
    """Extract action str from ActionMsg.

    Args:
        msg: control_pb2.ActionMsg containing action.

    Returns:
        action str extracted.
    """
    return msg.action


# --- Enum Helpers --- #
def get_enum_val(enum_obj: EnumTypeWrapper, name: str) -> int:
    """Get the int enum value of a zmq enum, given its name.

    Args:
        enum_obj: the zmq enum object, e.g. scan_pb2.ScopeState.
        name: the string name for the enum value, e.g. SS_SCANNING.

    Returns:
        the int value of this enum.
    """
    return enum_obj.Value(name)


def get_enum_str(enum_obj: EnumTypeWrapper, val: int) -> str:
    """Get the string of a zmq enum value, given said value.

    Args:
        enum_obj: the zmq enum object, e.g. scan_pb2.ScopeState.
        val: the int value of this enum.

    Returns:
        the string name for the enum value, e.g. SS_SCANNING.
    """
    return enum_obj.Name(val)


def is_str_in_enums(enum_obj: EnumTypeWrapper, name: str) -> bool:
    """Determine if a string corresponds to one of the enum values.

    Args:
        enum_obj: the zmq enum object, e.g. scan_pb2.ScopeState.
        name: the string name for the enum value, e.g. SS_SCANNING.

    Returns:
        true if the name corresponds to an enum value, false otherwise.
    """
    try:
        val = get_enum_val(enum_obj, name)
        return val is not None
    except ValueError:
        return False


# --- Control Specific Helpers --- #
def is_problem_in_problems_set(problem: control_pb2.ExperimentProblem,
                               problems_set: {control_pb2.ExperimentProblem}
                               ) -> bool:
    """Determine whether a given problem is in a problems set.

    This logic accounts for the fact that EP_NONE is equivalent to an empty
    problems set.

    Args:
        problem: the problem we are checking for.
        problems_set: the set we are looking in.

    Returns:
        True if problem is in the problems set (or EP_NONE is the problem and
            the problems_set is empty). False otherwise.
    """
    generic_component_request = (problem ==
                                 control_pb2.ExperimentProblem.EP_NONE)
    no_problems_and_generic_component_request = (
        generic_component_request and len(problems_set) == 0)
    solves_problem = problem in problems_set
    return no_problems_and_generic_component_request or solves_problem
