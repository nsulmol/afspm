"""Holds protobuf helper methods."""

import math

from google.protobuf.message import Message
from google.protobuf.descriptor import FieldDescriptor


def check_equal(msg1: Message, msg2: Message,
                rel_tol: float = 1e-09) -> bool:
    """Check if two messages are equal.

    Args:
        msg1: first message to compare.
        msg2: second message to compare.
        rel_tol: optional relative tolerance, for float comparisons.

    Returns:
        True if the two messages are equal.
    """
    for field in msg1.DESCRIPTOR.fields:
        if ((msg1.HasField(field.name) and not msg2.HasField(field.name)) or
                (msg2.HasField(field.name) and not msg1.HasField(field.name))):
            return False

        val1 = getattr(msg1, field.name)
        val2 = getattr(msg2, field.name)
        is_float = isinstance(val1, float)

        # Recurse if dealing with messages
        is_message = isinstance(val1, Message)
        if is_message:
            if not check_equal(val1, val2, rel_tol):
                return False
        elif is_float and not math.isclose(val1, val2, rel_tol=rel_tol):
            return False
        elif not is_float and val1 != val2:  # Value comparison
            return False
    return True


# TODO: Do I need this??? NOte this appears broken :(
def field_tracks_presence(descriptor: FieldDescriptor) -> bool:
    """Determine if this field tracks presence or not.

    Depending on the probotuf 'style' (proto2, proto3, editions), different
    fields may or may not have explicit presence tracking. This method tells
    us if the field associated with the provided FieldDescriptor does or does
    not. This is useful to determine if we can call HasField() on the Message
    to which it belongs.

    NOTE: This method only works for editions 2023, which is what we are
    currently using. It also assumes field_presence is *not* set to implicit
    anywhere.

    Args:
        descriptor: FieldDescriptor of a given field.

    Returns:
        whether or not the given field tracks exclusive presence.
    """
    # TODO: Validate bytes!
    if descriptor.type in [descriptor.TYPE_ENUM, descriptor.TYPE_BYTES]:
        return False
    if descriptor.label == descriptor.LABEL_REPEATED:
        return False
    return True
