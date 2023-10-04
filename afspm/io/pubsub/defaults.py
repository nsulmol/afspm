"""Holds default pubsub configuration variables.

Our 'default' pubsub involves:
- standard envelopes based on protos from the publisher;
- TODO: channel- and size-specific scan envelopes from pubsubcache.

Effectively, we want to allow a user to subscribe to specific channels and/or
scan sizes easily. Note that these envelopes always begin with Scan2d; thus,
you can subscribe to all of them by simply using the default envelope in
cache_logic.CacheLogic.get_envelope_for_proto.
"""

from .logic import cache_logic
from .logic import pbc_logic

SCAN_ID = 'Scan2d'  # Scan2d for default envelope
UPDATE_CACHE = cache_logic.update_cache
EXTRACT_PROTO = cache_logic.extract_proto

# Simple means each proto has an envelope.
# Scan means we create channel- and size-specific caches.
_SIMPLE_CACHE_KWARGS = {'cache_logic': pbc_logic.ProtoBasedCacheLogic()}
_SCAN_CACHE_KWARGS = {'cache_logic': pbc_logic.PBCScanLogic()}

# Same as above.
_SIMPLE_ENVELOPE_FOR_PROTO = cache_logic.CacheLogic.get_envelope_for_proto
_SCAN_ENVELOPE_FOR_PROTO = pbc_logic.PBCScanLogic.get_envelope_for_proto

PUBLISHER_ENVELOPE_FOR_PROTO = _SIMPLE_ENVELOPE_FOR_PROTO
PUBLISHER_ENVELOPE_KWARGS = None

PUBSUBCACHE_GET_ENVELOPE_FOR_PROTO = _SCAN_ENVELOPE_FOR_PROTO
PUBSUBCACHE_GET_ENVELOPE_KWARGS = None
PUBSUBCACHE_EXTRACT_PROTO_KWARGS = _SIMPLE_CACHE_KWARGS
PUBSUBCACHE_UPDATE_CACHE_KWARGS = _SCAN_CACHE_KWARGS

SUBSCRIBER_EXTRACT_PROTO_KWARGS = _SCAN_CACHE_KWARGS
SUBSCRIBER_UPDATE_CACHE_KWARGS = _SCAN_CACHE_KWARGS
