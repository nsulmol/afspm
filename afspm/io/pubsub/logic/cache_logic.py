"""Holds abc class and overarching helper methods for cache handling."""

from typing import Mapping
from abc import ABCMeta, abstractmethod
from collections.abc import Iterable
from google.protobuf.message import Message

from ...protos.generated import scan_pb2
from ...protos.generated import control_pb2
from ...protos.generated import feedback_pb2
from ...protos.generated import analysis_pb2

# A default proto-history list for a Last-Value Cache (LVC)
# Please update with new default messages created.
DEFAULT_PROTO_WITH_HIST_SEQ = ((scan_pb2.Scan2d(), 1),
                               (scan_pb2.ScopeStateMsg(), 1),
                               (control_pb2.ControlState(), 1),
                               (scan_pb2.ScanParameters2d(), 1),
                               (feedback_pb2.ZCtrlParameters(), 1),
                               (analysis_pb2.SpatialROIWithScoreList(), 1),
                               (analysis_pb2.SpatialPointWithScoreList(), 1))


class CacheLogic(metaclass=ABCMeta):
    """Abstract class for cache logic.

    This class defines the 3 expected methods for a CacheLogic class, which
    can be used by the equivalently named non-class methods.
    """

    @abstractmethod
    def extract_proto(self, msg: list[bytes]) -> Message:
        """Extract protobuf structure from provided message.

        Args:
            msg: list of bytes, presumed to correspond to a Protobuf
                message.

        Returns:
            A protobuf structure extracted from the message.
        """

    @abstractmethod
    def update_cache(self, proto: Message, cache: Mapping[str, Iterable]
                     ):
        """Update the provided cache with the provided envelope and proto.

        Args:
            proto: protobuf structure linked to the envelope.
            cache: mapping for storing the messages received. of the form:
                envelope: list[proto] (for key:val). Note that the suggested
                'list' type here is a dequeue, as it allows a size definition
                (and will pop elements from the back if you exceed the size).
        """

    @staticmethod
    def get_envelope_for_proto(proto: Message) -> str:
        """Given a protobuf structure, return the appropriate envelope string.

        This envelope will be used for caching data.

        Args:
            proto: protobuf structure whose envelope we wish to determine.

        Returns:
            associated envelope of the proto.
        """
        return type(proto).__name__  # Treat class name as topic UUID

    @staticmethod
    def create_default_proto(proto: Message) -> Message:
        """To have default instance to build off of."""
        return proto.__class__()


def extract_proto(msg: list[bytes], cache_logic: CacheLogic) -> Message:
    """Non-class method for extracting proto given a CacheLogic instance.

    See CacheLogic.extract_proto() for more info.
    """
    return cache_logic.extract_proto(msg)


def update_cache(proto: Message, cache: dict[str, Iterable],
                 cache_logic: CacheLogic):
    """Non-class method for updating the cache for a particular proto.

    see CacheLogic.update_cache() for more info.
    """
    cache_logic.update_cache(proto, cache)
