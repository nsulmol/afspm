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
def proto_5nm_hist():
    return 2


@pytest.fixture
def proto_10nm_hist():
    return 5


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
def pbc_with_roi_logic(proto_5nm, proto_10nm, proto_5nm_hist, proto_10nm_hist):
    def_hist_list = list(cl.DEFAULT_PROTO_WITH_HIST_SEQ)
    def_hist_list.append((proto_5nm, proto_5nm_hist))
    def_hist_list.append((proto_10nm, proto_10nm_hist))
    return pbc.PBCScanLogic(def_hist_list)


def test_extract_proto(proto_5nm, pbc_logic, pbc_long_history_logic):
    """Validate we can extract a proto properly."""

    for logic in [pbc_logic, pbc_long_history_logic]:
        envelope = logic.get_envelope_for_proto(proto_5nm)
        msg = [envelope.encode(), proto_5nm.SerializeToString()]
        extracted_proto = logic.extract_proto(msg)
        assert proto_5nm == extracted_proto


def test_update_cache_lvc(cache, proto_5nm, proto_10nm, pbc_logic):
    """Validate we can use the cache properly."""
    logic = pbc_logic
    for proto in [proto_5nm, proto_10nm]:
        envelope = logic.get_envelope_for_proto(proto)
        logic.update_cache(proto, cache)
        assert proto in cache[envelope]


def test_update_cache_longer_history(cache, proto_5nm, pbc_long_history_logic):
    max_len = 5
    logic = pbc_long_history_logic

    envelope = logic.get_envelope_for_proto(proto_5nm)
    logic.update_cache(proto_5nm, cache)
    assert proto_5nm in cache[envelope]

    # Test up to/before popping
    names = ['a', 'b', 'c', 'd']
    expected_cache_names = deque([proto_5nm.params.name],
                                 maxlen=max_len)
    for name in names:
        proto = copy.deepcopy(proto_5nm)
        proto.params.name = name
        expected_cache_names.append(name)
        logic.update_cache(proto, cache)

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
        logic.update_cache(proto, cache)

        cache_names = [x.params.name for x in cache[envelope]]
        for cn in expected_cache_names:
            assert cn in cache_names


def test_pbc_with_roi_logic(cache, proto_5nm, proto_10nm,
                            proto_5nm_hist, proto_10nm_hist,
                            pbc_with_roi_logic):
    """Validate we can use the cache properly."""
    protos = [proto_5nm, proto_10nm]
    hists = [proto_5nm_hist, proto_10nm_hist]

    logic = pbc_with_roi_logic
    for proto in protos:
        envelope = logic.get_envelope_for_proto(proto)
        logic.update_cache(proto, cache)
        assert proto in cache[envelope]

    # Because we are using ROI logic, the original proto should still be there!
    envelope = logic.get_envelope_for_proto(proto_5nm)
    assert proto_5nm in cache[envelope]

    # Now, validate that our history is correct for each.
    cache = {}
    for (proto, hist) in zip(protos, hists):
        expected_hist = []
        for cnt in range(0, hist):
            tmp = copy.deepcopy(proto)
            tmp.params.name += str(cnt)

            logic.update_cache(tmp, cache)
            expected_hist.append(tmp)

        envelope = logic.get_envelope_for_proto(proto)
        for idx, cache_val in enumerate(cache[envelope]):
            assert cache_val == expected_hist[idx]

        tmp = copy.deepcopy(proto)
        tmp.params.name = 'last_guy'
        logic.update_cache(tmp, cache)
        # Append to end and remove first item (simulating deque)
        expected_hist = expected_hist[1:]
        expected_hist.append(tmp)
        for idx, cache_val in enumerate(cache[envelope]):
            assert cache_val == expected_hist[idx]
