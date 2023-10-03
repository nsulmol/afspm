"""Proto-Based Cache Logic classes."""

import logging
import copy
from collections.abc import Iterable
from collections import deque
from google.protobuf.message import Message

from .cache_logic import CacheLogic, DEFAULT_PROTO_WITH_HIST_SEQ
from ..protos.generated import scan_pb2
from .. import common


logger = logging.getLogger(__name__)


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
            envelope = self.get_envelope_for_proto(proto)
            self.envelope_to_history_map[envelope] = history
            self.envelope_to_proto_map[envelope] = self.create_default_proto(
                proto)

    def extract_proto(self, msg: list[bytes]) -> Message:
        envelope, contents = msg
        proto = copy.deepcopy(self.envelope_to_proto_map[envelope.decode()])
        proto.ParseFromString(contents)
        return proto

    def update_cache(self, proto: Message, cache: dict[str, Iterable]
                     ):
        envelope = self.get_envelope_for_proto(proto)
        if envelope not in cache:
            cache[envelope] = deque(maxlen=self.envelope_to_history_map[
                envelope])
        cache[envelope].append(proto)


class PBCScanLogic(ProtoBasedCacheLogic):
    """Proto-based-cache with special handling for Scan2d.

    This expands upon ProtoBasedCacheLogic, to add individual caches (and
    envelopes) for different physical sizes of Scan2d (i.e. a different
    envelope for each different size) and different scan channels (i.e.
    channels of a scan).

    Attributes:
        scan_id: holds string uuid for Scan2d, for help parsing.
    """
    scan_id = ProtoBasedCacheLogic.get_envelope_for_proto(
        scan_pb2.Scan2d())
    divider = '_'

    def __init__(self, proto_with_history_list: list[(Message, int)] =
                 DEFAULT_PROTO_WITH_HIST_SEQ,
                 default_scan_history: int = 1, **kwargs):

        """Override to force default Scan2d history and protos."""
        super().__init__(proto_with_history_list, **kwargs)

        # Even if this was set in proto_with_history_list, override with
        # explicit input variable.
        self.envelope_to_history_map[self.scan_id] = default_scan_history

        if self.scan_id not in self.envelope_to_proto_map:
            self.envelope_to_proto_map[self.scan_id] = (
                self.create_default_proto(scan_pb2.Scan2d()))

    @staticmethod
    def get_envelope_for_proto(proto: Message,
                               force_parent: bool = False) -> str:
        """ Overrides standard mechanism for cache_case.

        Args:
            proto: protobuf message.
            force_parent: if true, we do not perform the special ROI caching.
                This allows us to store non-specific Scan2d information
                easily (such as the cache size).
        """
        if (type(proto).__name__ == PBCScanLogic.scan_id and
                not force_parent):
            return (PBCScanLogic.scan_id + PBCScanLogic.divider +
                    proto.channel + PBCScanLogic.divider +
                    str(proto.params.spatial.roi.size.x))
        return ProtoBasedCacheLogic.get_envelope_for_proto(proto)

    def update_cache(self, proto: Message, cache: dict[str, Iterable]
                     ):
        """Override: if specific scan2d not in maps, we use default."""
        try:
            super().update_cache(proto, cache)
        except KeyError as exc:
            envelope = self.get_envelope_for_proto(proto)
            if self.scan_id not in envelope:
                raise exc

            # Non-specific Scan2d like provided. Let's try with the default.
            if envelope not in cache:
                cache[envelope] = deque(maxlen=self.envelope_to_history_map[
                    self.scan_id])
            cache[envelope].append(proto)


def create_roi_proto_hist_list(sizes_with_hist_list:
                               list[tuple[tuple[float, float], int]]
                               ) -> list[(Message, int)]:
    """Helper to create a proto-with-hist list for special ROIs.

    Args:
        sizes_with_hist_list: list of (size, cache_length), where size is
            (x, y).

    Returns:
        A proto-history list, for instantiation of a PBCWithROi cache logic.
    """

    proto_with_hist_list = list(DEFAULT_PROTO_WITH_HIST_SEQ)
    for (size, hist) in sizes_with_hist_list:
        scan_params = common.create_scan_params_2d(size=[size[0], size[1]])
        scan_2d = common.create_scan_2d(scan_params=scan_params)
        proto_with_hist_list.append((scan_2d, hist))
    return proto_with_hist_list


def create_roi_scan_envelope(size: tuple[float, float]) -> str:
    """Helper to create envelope for Scan2d of specific size."""
    scan_params = common.create_scan_params_2d(size=[size[0], size[1]])
    scan_2d = common.create_scan_2d(scan_params=scan_params)
    return PBCScanLogic.get_envelope_for_proto(scan_2d)
