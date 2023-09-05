"""Holds common I/O logic."""

from google.protobuf.internal.enum_type_wrapper import EnumTypeWrapper


KILL_SIGNAL = "KILL"
ALL_ENVELOPE = ""
ALL_ENVELOPE_LOG = "ALL"


def get_enum_val(enum_obj: EnumTypeWrapper, name: str) -> int:
    """Gets the int enum value of a zmq enum, given its name.

    Args:
        enum_obj: the zmq enum object, e.g. scan_pb2.ScanState.
        name: the string name for the enum value, e.g. SS_SCANNING.

    Returns:
        the int value of this enum.
    """
    return enum_obj.Value(name)

def get_enum_str(enum_obj: EnumTypeWrapper, val: int) -> str:
    """Gets the string of a zmq enum value, given said value.

    Args:
        enum_obj: the zmq enum object, e.g. scan_pb2.ScanState.
        val: the int value of this enum.

    Returns:
        the string name for the enum value, e.g. SS_SCANNING.
    """
    return enum_obj.Name(val)


def is_str_in_enums(enum_obj: EnumTypeWrapper, name: str) -> bool:
    """Determines if a string corresponds to one of the enum values.

    Args:
        enum_obj: the zmq enum object, e.g. scan_pb2.ScanState.
        name: the string name for the enum value, e.g. SS_SCANNING.

    Returns:
        true if the name corresponds to an enum value, false otherwise.
    """
    try:
        val = get_enum_val(name)
        return True
    except ValueError:
        return False
