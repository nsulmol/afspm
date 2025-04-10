"""Holds protobuf helper methods."""

import math

from google.protobuf.message import Message


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

        if is_float and not math.isclose(val1, val2, rel_tol=rel_tol):
            return False
        if not is_float and val1 != val2:  # Value comparison
            return False
    return True
