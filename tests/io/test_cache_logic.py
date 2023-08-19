"""Test cache creation logic."""

import copy
import pytest
from collections import deque

from afspm.io.cache import cache_logic as cl
from afspm.io.cache import pbc_logic as pbc
from afspm.io.protos.generated import scan_pb2
from afspm.io.protos.generated import control_pb2


@pytest.fixture
def cache():
    return {}

@pytest.fixture
def proto_5nm():
    proto = scan_pb2.Scan2d()
    proto.params.name = 'banana'
    proto.params.spatial.roi.size.x = 5
    return proto

@pytest.fixture
def proto_10nm():
    proto = scan_pb2.Scan2d()
    proto.params.name = 'hammock'
    proto.params.spatial.roi.size.x = 10
    return proto


@pytest.fixture
def pbc_logic():
    return pbc.ProtoBasedCacheLogic()  # LVC is default


@pytest.fixture
def pbc_long_history_logic():
    max_len = 5
    # Create list from our global variable (due to conversion, new object).
    proto_history_list = list(cl.DEFAULT_PROTO_WITH_HIST_SEQ)
    proto_history_list[0] = (scan_pb2.Scan2d(), max_len)
    return pbc.ProtoBasedCacheLogic(proto_history_list)


@pytest.fixture
def pbc_with_roi_logic():
    return pbc.PBCWithROILogic()  # LVC is default


def test_extract_proto(proto_5nm, pbc_logic, pbc_long_history_logic):
    """Validate we can extract a proto properly."""

    for logic in [pbc_logic, pbc_long_history_logic]:
        envelope = logic.create_envelope_from_proto(proto_5nm)
        msg = [envelope.encode(), proto_5nm.SerializeToString()]
        extracted_proto = logic.extract_proto(msg)
        assert proto_5nm == extracted_proto


def test_update_cache_lvc(cache, proto_5nm, proto_10nm, pbc_logic):
    """Validate we can use the cache properly."""
    logic = pbc_logic
    for proto in [proto_5nm, proto_10nm]:
        envelope = logic.create_envelope_from_proto(proto)
        cache = logic.update_cache(envelope, proto, cache)
        assert proto in cache[envelope]


def test_update_cache_longer_history(cache, proto_5nm, pbc_long_history_logic):
    max_len = 5
    logic = pbc_long_history_logic

    envelope = logic.create_envelope_from_proto(proto_5nm)
    cache = logic.update_cache(envelope, proto_5nm, cache)
    assert proto_5nm in cache[envelope]

    # Test up to/before popping
    names = ['a', 'b', 'c', 'd']
    expected_cache_names = deque([proto_5nm.params.name],
                                 maxlen=max_len)
    for name in names:
        proto = copy.deepcopy(proto_5nm)
        proto.params.name = name
        expected_cache_names.append(name)
        cache = logic.update_cache(envelope, proto, cache)

        cache_names = [x.params.name for x in cache[envelope]]
        for cn in expected_cache_names:
            assert cn in cache_names

    # Test that popping works!
    names = ['1', '2', '3', '4', '5']
    expected_cache_names = []
    for name in names:
        proto = copy.deepcopy(proto_5nm)
        proto.params.name = name
        expected_cache_names.append(name)
        cache = logic.update_cache(envelope, proto, cache)

        cache_names = [x.params.name for x in cache[envelope]]
        for cn in expected_cache_names:
            assert cn in cache_names


def test_pbc_with_roi_logic(cache, proto_5nm, proto_10nm, pbc_with_roi_logic):
    """Validate we can use the cache properly."""
    logic = pbc_with_roi_logic
    for proto in [proto_5nm, proto_10nm]:
        envelope = logic.create_envelope_from_proto(proto)
        cache = logic.update_cache(envelope, proto, cache)
        assert proto in cache[envelope]

    # Because we are using ROI logic, the original proto should still be there!
    envelope = logic.create_envelope_from_proto(proto_5nm)
    assert proto_5nm in cache[envelope]
