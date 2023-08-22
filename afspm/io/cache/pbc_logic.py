"""Proto-Based Cache Logic classes."""

import string
from typing import Mapping
from collections.abc import Iterable
from collections import deque
from google.protobuf.message import Message

from .cache_logic import CacheLogic, DEFAULT_PROTO_WITH_HIST_SEQ
from ..protos.generated import scan_pb2


class ProtoBasedCacheLogic(CacheLogic):
    """The simplest cache: a 1-to-1 between topic and proto.

    Attributes:
        proto_to_history_map: mapping indicating cache size for each proto.
        proto_to_envelope_map: mapping from proto to envelope.
        envelope_to_proto_map: mapping from envelope to proto.
    """

    def __init__(self, proto_with_history_list: list[(Message, int)] =
                 DEFAULT_PROTO_WITH_HIST_SEQ, **kwargs):

        self.envelope_to_history_map = {}
        self.envelope_to_proto_map = {}

        for (proto, history) in proto_with_history_list:
            # Storing default proto in envelope_to_proto_map, but allowing
            # envelope_to_history_map to use the one provided. This allows
            # having different cache sizes for different versions of a
            # specific proto. Note that for this, the user must know all
            # potential versions of a specific proto will be called!
            envelope = self.create_envelope_from_proto(proto)
            self.envelope_to_history_map[envelope] = history
            self.envelope_to_proto_map[envelope] = self.create_default_proto(
                proto)

    def extract_proto(self, msg: list[bytes]) -> Message:
        envelope, contents = msg
        proto = self.envelope_to_proto_map[envelope.decode()]
        proto.ParseFromString(contents)
        return proto

    def update_cache(self, envelope: str, proto: Message,
                     cache: dict[str, Iterable]) -> dict[str, Iterable]:
        # We do not care about the envelope it was passed in, using
        # our own as key.
        envelope = self.create_envelope_from_proto(proto)
        if envelope not in cache:
            cache[envelope] = deque(maxlen=self.envelope_to_history_map[
                envelope])
        cache[envelope].append(proto)
        return cache


class PBCWithROILogic(ProtoBasedCacheLogic):
    """Proto-based-cache with special handling for Scan2d.

    This expands upon ProtoBasedCacheLogic, to add individual caches (and
    envelopes) for different physical sizes of Scan2d (i.e. a different
    envelope for each different size).

    Attributes:
        scan_id: holds string uuid for Scan2d, for help parsing.
    """
    scan_id = ProtoBasedCacheLogic.create_envelope_from_proto(
        scan_pb2.Scan2d())

    def __init__(self, proto_with_history_list: list[(Message, int)] =
                 DEFAULT_PROTO_WITH_HIST_SEQ):
        """ Diff with super(): we use internal envelope."""
        self.envelope_to_history_map = {}
        self.envelope_to_proto_map = {}

        for (proto, history) in proto_with_history_list:
            # Storing default proto in envelope_to_proto_map, but allowing
            # envelope_to_history_map to use the one provided. This allows
            # having different cache sizes for different versions of a
            # specific proto. Note that for this, the user must know all
            # potential versions of a specific proto will be called!
            envelope = self.create_envelope_from_proto(proto, True)
            self.envelope_to_history_map[envelope] = history
            self.envelope_to_proto_map[envelope] = self.create_default_proto(
                proto)

    @staticmethod
    def create_envelope_from_proto(proto: Message,
                                   internal_use: bool = False) -> str:
        """ Overrides standard mechanism for cache_case.

        Args:
            proto: protobuf message.
            internal_use: if true, we do not perform the special ROI caching.
                This allows us to store non-specific Scan2d information
                easily (such as the cache size).
        """
        if (type(proto).__name__ == PBCWithROILogic.scan_id and
                not internal_use):
            return (PBCWithROILogic.scan_id + '_'
                    + str(proto.params.spatial.roi.size.x))
        return ProtoBasedCacheLogic.create_envelope_from_proto(proto)

    def extract_proto(self, msg: list[bytes]) -> Message:
        """Overrides standard mechanism for ROI-based Scan2ds.

        Use 'generic' envelope if dealing with scan_id, since our internal
        maps were created using it. This avoided having to know all possible
        instantiations of scan_id.
        """
        envelope = msg[0].decode()
        envelope = self.scan_id if self.scan_id else envelope
        contents = msg[1:]

        proto = self.envelope_to_proto_map[envelope]
        proto.ParseFromString(contents)
        return proto

    def update_cache(self, envelope: str, proto: Message,
                     cache: dict[str, Iterable]) -> dict[str, Iterable]:
        """Overrides standard mechanism for ROI-based Scan2ds.

        Use 'generic' envelope if dealing with scan_id, since our internal
        maps were created using it. This avoided having to know all possible
        instantiations of scan_id.
        """
        # We do not care about the envelope it was passed in, using
        # our own as key.
        envelope = self.create_envelope_from_proto(proto)
        internal_envelope = (self.scan_id if self.scan_id in envelope
                             else envelope)

        if envelope not in cache:
            cache[envelope] = deque(maxlen=self.envelope_to_history_map[
                internal_envelope])
        cache[envelope].append(proto)
        return cache
