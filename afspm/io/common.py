"""Holds common I/O logic."""
import time

from google.protobuf.internal.enum_type_wrapper import EnumTypeWrapper

from ..io.protos.generated import scan_pb2
from ..io.protos.generated import geometry_pb2


# --- Common envelope/signal stuff --- #
KILL_SIGNAL = "KILL"
ALL_ENVELOPE = ""
ALL_ENVELOPE_LOG = "ALL"


# --- Good defaults --- #
REQUEST_TIMEOUT_MS = 250  # Linked to TCP delay
POLL_TIMEOUT_MS = 25
LOOP_SLEEP_S = 0.1  # 100 ms
HEARTBEAT_PERIOD_S = 5
BEATS_BEFORE_DEAD = 5


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
                          phys_units: str = None,
                          data_shape: tuple[int, int] = None,
                          data_units: str = None,
                          ) -> scan_pb2.ScanParameters2d:
    """Create ScanParameters2d object.

    This is merely a helper, to avoid annoyances with the protobuf data
    initialization.

    Args:
        top_left: physical roi, top-left position.
        size: physical roi, size.
        phys_units: units of the physical dimensions.
        data_shape: 2D shape of the 2D scan.
        data_units: units of the z-dimension.
        channel_name: name of the channel this data comes from.

    Returns:
        Initialized ScanParameters2d instance.
    """
    top_left = (geometry_pb2.Point2d(x=top_left[0], y=top_left[1]) if top_left
                else None)
    size = geometry_pb2.Size2d(x=size[0], y=size[1]) if size else None
    roi = geometry_pb2.Rect2d(top_left=top_left, size=size)
    da_shape = (geometry_pb2.Size2u(x=data_shape[0], y=data_shape[1])
                if data_shape else None)
    spatial_aspects = scan_pb2.SpatialAspects(roi=roi,
                                              units=phys_units)
    data_aspects = scan_pb2.DataAspects(shape=da_shape, units=data_units)
    return scan_pb2.ScanParameters2d(spatial=spatial_aspects,
                                     data=data_aspects)


# --- Enum Helpers --- #
def get_enum_val(enum_obj: EnumTypeWrapper, name: str) -> int:
    """Get the int enum value of a zmq enum, given its name.

    Args:
        enum_obj: the zmq enum object, e.g. scan_pb2.ScanState.
        name: the string name for the enum value, e.g. SS_SCANNING.

    Returns:
        the int value of this enum.
    """
    return enum_obj.Value(name)


def get_enum_str(enum_obj: EnumTypeWrapper, val: int) -> str:
    """Get the string of a zmq enum value, given said value.

    Args:
        enum_obj: the zmq enum object, e.g. scan_pb2.ScanState.
        val: the int value of this enum.

    Returns:
        the string name for the enum value, e.g. SS_SCANNING.
    """
    return enum_obj.Name(val)


def is_str_in_enums(enum_obj: EnumTypeWrapper, name: str) -> bool:
    """Determine if a string corresponds to one of the enum values.

    Args:
        enum_obj: the zmq enum object, e.g. scan_pb2.ScanState.
        name: the string name for the enum value, e.g. SS_SCANNING.

    Returns:
        true if the name corresponds to an enum value, false otherwise.
    """
    try:
        val = get_enum_val(enum_obj, name)
        return val is not None
    except ValueError:
        return False
