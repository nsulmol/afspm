"""Proto-Based Cache Logic classes."""

import numpy as np
import logging
from itertools import zip_longest
# from typing import Any
import copy
from collections.abc import Iterable
from collections import deque
from google.protobuf.message import Message

from .cache_logic import CacheLogic, DEFAULT_PROTO_WITH_HIST_SEQ
from ...protos.generated import scan_pb2
from ...protos.generated import spec_pb2
from ... import common


logger = logging.getLogger(__name__)


DIVIDER = '_'  # Envelope divider


class ProtoBasedCacheLogic(CacheLogic):
    """The simplest cache: a 1-to-1 between topic and proto.

    Attributes:
        proto_to_history_map: mapping indicating cache size for each proto.
        proto_to_envelope_map: mapping from proto to envelope.
        envelope_to_proto_map: mapping from envelope to proto.
    """

    def __init__(self, proto_with_history_list: list[(Message, int)] =
                 DEFAULT_PROTO_WITH_HIST_SEQ):
        """Init our Proto-Based cache logic."""
        self.envelope_to_history_map = {}
        self.envelope_to_proto_map = {}

        for (proto, history) in proto_with_history_list:
            # Storing default proto in envelope_to_proto_map, but allowing
            # envelope_to_history_map to use the one provided. This allows
            # having different cache sizes for different versions of a
            # specific proto.
            envelope = self.get_envelope_for_proto(proto)
            self.envelope_to_history_map[envelope] = history
            self.envelope_to_proto_map[envelope] = self.create_default_proto(proto)

    def extract_proto(self, msg: list[bytes]) -> Message:
        """Overload parent."""
        envelope, contents = msg
        envelope = envelope.decode()

        if envelope not in self.envelope_to_proto_map:
            envelope = get_closest_match(
                envelope, list(self.envelope_to_proto_map.keys()))
        try:
            proto = self.envelope_to_proto_map[envelope]
        except KeyError:
            msg = ('Could not find envelope or closest match envelope in '
                   'envelope_to_proto_map. Make sure '
                   'DEFAULT_PROTO_WITH_HIST_SEQ includes all base protos.')
            logger.error(msg)
            raise KeyError(msg)

        proto = copy.deepcopy(proto)
        proto.ParseFromString(contents)
        return proto

    def update_cache(self, proto: Message, cache: dict[str, Iterable]):
        """Overload parent."""
        envelope = self.get_envelope_for_proto(proto)
        if envelope not in cache:
            try:
                # Add deque to cache of provided history (for this envelope)
                cache[envelope] = deque(maxlen=self.envelope_to_history_map[
                    envelope])
                logger.trace(f'for envelope: {envelope}, found in history map.')
            except KeyError:
                # See if there is a close match in our provided env-hist
                # map, to use in order to create the queue for this env.
                match_env = get_closest_match(
                    envelope, list(self.envelope_to_history_map.keys()))

                if match_env is None:
                    msg = (f'No match found for {envelope} in provided keys. '
                           'This should not happen!')
                    logger.error(msg)
                    raise KeyError(msg)

                maxlen = self.envelope_to_history_map[match_env]
                logger.trace(f'for envelope: {envelope}, found {match_env}, '
                             f'adding cache of size {maxlen}.')
                cache[envelope] = deque(maxlen=maxlen)
        cache[envelope].append(proto)


class PBCScanLogic(ProtoBasedCacheLogic):
    """Proto-based-cache with special handling for Scan2d and Spec1d.

    This expands upon ProtoBasedCacheLogic, to add individual caches (and
    envelopes) for different physical sizes of Scan2d (i.e. a different
    envelope for each different size) and different scan channels (i.e.
    channels of a scan). For specs, it creates individual caches (and
    envelopes) for different Spec1d types.

    Attributes:
        scan_id: holds string uuid for Scan2d, for help parsing.
        spec_id: holds string uuid for Spec1d, for help parsing.
    """

    BASE_SCAN_ID = ProtoBasedCacheLogic.get_envelope_for_proto(
        scan_pb2.Scan2d())
    BASE_SPEC_ID = ProtoBasedCacheLogic.get_envelope_for_proto(
        spec_pb2.Spec1d())

    def __init__(self, proto_with_history_list: list[(Message, int)] =
                 DEFAULT_PROTO_WITH_HIST_SEQ,
                 default_scan_history: int = 1,
                 default_spec_history: int = 1, **kwargs):
        """Override to force default Scan2d history and protos."""
        super().__init__(proto_with_history_list, **kwargs)

        scan_id = self.get_envelope_for_proto(scan_pb2.Scan2d())
        spec_id = self.get_envelope_for_proto(spec_pb2.Spec1d())

        # Even if this was set in proto_with_history_list, override with
        # explicit input variable.
        self.envelope_to_history_map[scan_id] = default_scan_history
        self.envelope_to_history_map[spec_id] = default_spec_history

        if scan_id not in self.envelope_to_proto_map:
            self.envelope_to_proto_map[scan_id] = (
                self.create_default_proto(scan_pb2.Scan2d()))

        if spec_id not in self.envelope_to_proto_map:
            self.envelope_to_proto_map[spec_id] = (
                self.create_default_proto(spec_pb2.Spec1d()))

    @staticmethod
    def get_envelope_for_proto(proto: Message,
                               force_parent: bool = False) -> str:
        """Override standard mechanism for cache_case.

        Args:
            proto: protobuf message.
            force_parent: if true, we do not perform the special ROI caching.
                This allows us to store non-specific Scan2d information
                easily (such as the cache size).
        """
        # TODO: Change order, size before channel!
        if (type(proto).__name__ == PBCScanLogic.BASE_SCAN_ID and
                not force_parent):
            envelope = PBCScanLogic.BASE_SCAN_ID
            envelope += (DIVIDER +
                         str(np.round(proto.params.spatial.roi.size.x))
                         if proto.params.spatial.roi.size.HasField('x')
                         else DIVIDER)
            envelope += (DIVIDER + proto.channel
                         if proto.HasField('channel')
                         else DIVIDER)
            return envelope
        if (type(proto).__name__ == PBCScanLogic.BASE_SPEC_ID and
                not force_parent):
            envelope = PBCScanLogic.BASE_SPEC_ID
            envelope += (DIVIDER + proto.type
                         if proto.HasField('type')
                         else DIVIDER)
            return envelope
        return ProtoBasedCacheLogic.get_envelope_for_proto(proto)


def create_roi_proto_hist_list(sizes_with_hist_list:
                               list[tuple[tuple[float, float], int]],
                               channels_list: list[str] | None = None
                               ) -> list[(Message, int)]:
    """Create a proto-with-hist list for special ROIs.

    Args:
        sizes_with_hist_list: list of (size, cache_length), where size is
            (x, y).
        channels_list: list of str of (size), to indicate the channel of
            interest for each of the sizes. If you want to consider all
            channels, leave this as None.

    Returns:
        A proto-history list, for instantiation of a PBCScanLogic.
    """
    if channels_list is not None:
        assert len(sizes_with_hist_list) == len(channels_list)

    # Appending to base/default.
    proto_with_hist_list = list(DEFAULT_PROTO_WITH_HIST_SEQ)
    for idx, (size, hist) in enumerate(sizes_with_hist_list):
        scan_params = common.create_scan_params_2d(size=[size[0], size[1]])
        channel = channels_list[idx] if channels_list else None
        scan_2d = scan_pb2.Scan2d(params=scan_params,
                                  channel=channel)
        proto_with_hist_list.append((scan_2d, hist))
    return proto_with_hist_list


def create_roi_scan_envelope(size: tuple[float, float],
                             channel: str = None) -> str:
    """Create envelope for Scan2d of specific size (and channel)."""
    scan_params = common.create_scan_params_2d(size=[size[0], size[1]])
    scan_2d = scan_pb2.Scan2d(params=scan_params)
    if channel:
        scan_2d.channel = channel
    return PBCScanLogic.get_envelope_for_proto(scan_2d)


def create_spec_envelope(type: str) -> str:
    """Create envelope for Spec1d of specific type."""
    spec = spec_pb2.Spec1d()
    spec.type = type
    return PBCScanLogic.get_envelope_for_proto(spec)


def get_closest_match(envelope: str, keys: list[str]
                      ) -> str | None:
    """Given an envelope, get the closest matching envelope to it.

     The envelopes are assumed to be in the format:
         ${type(proto)}_${B}_${C}...
    where the substr '${type(proto)}' is the proto type, '_' is a common
    divider, and all subsequent substrs are CacheLogic-specific
    differentiators.

    We define a 'matching' envelope as one where the proto types match. After
    this, we need a mechanism to score the envelopes of the same type, in order
    to decide which to select. To do so, we split both strs into substrs (using
    the divider to split), determine matches, and then sum the matches to
    obtain a score.

    Determining matches considers two aspects:
    1. If the two substrs are the same, they are considered an exact match.
    2. If one of the substrs is empty (the zmq equivalent of regexp *), we
    consider them as a 'soft' match.

    To score envelope pairs, we do a sum over the matches, weighting exact
    matches as 2x those of soft matches:
        score = 2*sum(exact_matches) + sum(soft_matches)

    The envelope with the highest score is considered the closest match. In
    equal-score cases, the first in the list of keys is accepted.

    Args:
        envelope: the envelope we are comparing against.
        keys: the list of keys for which we want to find the 'closest' match.

    Returns:
        the closest match or None if no match found.
    """
    match_env = None
    match_key_score = 0
    env_substrs = envelope.split(DIVIDER)

    logger.trace(f'examining {envelope}')
    for key in keys:
        logger.trace(f'for key: {key}')
        key_substrs = key.split(DIVIDER)

        if env_substrs[0] != key_substrs[0]:
            continue  # Not dealing with the same proto types.

        matching_substrs = [env_substr == key_substr and env_substr != ''
                            for env_substr, key_substr in
                            zip(env_substrs, key_substrs, strict=True)]
        regex_substrs = [env_substr == '' or key_substr == ''
                         for env_substr, key_substr in
                         zip(env_substrs, key_substrs, strict=True)]

        logger.trace(f'matching_substrs: {matching_substrs}')
        logger.trace(f'regex_substrs: {regex_substrs}')

        # Get score for this comparison
        score = 2*sum(matching_substrs) + sum(regex_substrs)
        logger.trace(f'score: {score}')

        if score > match_key_score:
            match_key_score = score
            match_env = key

    logger.trace(f'selected {match_env}')
    return match_env
