""" Test cache creation logic."""

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
    proto.parameters.name = 'banana'
    proto.parameters.spatial_roi.size.x = 5
    return proto

@pytest.fixture
def proto_10nm():
    proto = scan_pb2.Scan2d()
    proto.parameters.name = 'hammock'
    proto.parameters.spatial_roi.size.x = 10
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
    expected_cache_names = deque([proto_5nm.parameters.name],
                                 maxlen=max_len)
    for name in names:
        proto = copy.deepcopy(proto_5nm)
        proto.parameters.name = name
        expected_cache_names.append(name)
        cache = logic.update_cache(envelope, proto, cache)

        cache_names = [x.parameters.name for x in cache[envelope]]
        for cn in expected_cache_names:
            assert cn in cache_names

    # Test that popping works!
    names = ['1', '2', '3', '4', '5']
    expected_cache_names = []
    for name in names:
        proto = copy.deepcopy(proto_5nm)
        proto.parameters.name = name
        expected_cache_names.append(name)
        cache = logic.update_cache(envelope, proto, cache)

        cache_names = [x.parameters.name for x in cache[envelope]]
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



# class TestPBCLogicLVC:
#     """ Test the Last Value Cache version of Proto-Based Caching.

#     We validate we can extract a proto and update the cache without
#     issues.
#     """

#     # def __init__(self):
#     #     self.logic = cl.ProtoBasedCacheLogic()  # LVC is default

#     #     self.proto = scan_pb2.Scan2d()
#     #     self.proto.parameters.name = 'banana'
#     #     self.proto.parameters.spatial_roi.size.x = 5
#     #     self.envelope = self.logic.create_envelope_from_proto(self.proto)

#     #     self.cache = {}


#     def test_extract_proto(self, proto_5nm):
#         """Validate we can extract a proto properly."""
#         msg = [self.envelope.encode(), self.proto.SerializeToString()]
#         extracted_proto = self.logic.extract_proto(msg)
#         assert self.proto == extracted_proto

#     def test_update_cache(self):
#         """Validate we can use the cache properly."""
#         # Add 1st proto to test
#         self.cache = self.logic.update_cache(self.envelope,
#                                                           self.proto,
#                                                           self.cache)
#         assert self.proto in self.cache[self.envelope]

#         # Create 2nd proto to test
#         proto = copy.deepcopy(self.proto)
#         proto.parameters.name = 'hammock'
#         proto.parameters.spatial_roi.size.x = 10
#         envelope = self.logic.create_envelope_from_proto(proto)
#         self.cache = self.logic.update_cache(envelope, proto,
#                                                           self.cache)
#         assert proto in self.cache[envelope]


# class TestPBCLogicLongerHistory(TestPBCLogicLVC):
#     """ Test Proto-Based Caching with longer history for Scan2ds.

#     We use a larger-than-1 cache for Scan2d, and validate the caching
#     mechanism works well.
#     """
#     # max_len = 5
#     # # Create list from our global variable (due to conversion, new object).
#     # proto_history_list = list(cl.DEFAULT_PROTO_WITH_HIST_SEQ)
#     # proto_history_list[0] = (scan_pb2.Scan2d(), max_len)
#     # logic = cl.ProtoBasedCacheLogic(proto_history_list)

#     # proto = scan_pb2.Scan2d()
#     # proto.parameters.name = 'banana'  # Change one thing to validate
#     # envelope = logic.create_envelope_from_proto(proto)
#     # msg = [envelope.encode(), proto.SerializeToString()]

#     def __init__(self):
#         super().__init__()  # Start with everything the same, then overwrite
#         max_len = 5
#         # Create list from our global variable (due to conversion, new object).
#         proto_history_list = list(cl.DEFAULT_PROTO_WITH_HIST_SEQ)
#         proto_history_list[0] = (scan_pb2.Scan2d(), max_len)
#         self.logic = cl.ProtoBasedCacheLogic(proto_history_list)

#     def test_update_cache(self):
#         self.cache = self.logic.update_cache(self.envelope,
#                                                           self.proto,
#                                                           self.cache)
#         assert self.proto in self.cache[self.envelope]

#         # Test up to/before popping
#         names = ['a', 'b', 'c', 'd']
#         expected_cache_names = deque([self.proto.parameters.name],
#                                      maxlen=self.max_len)
#         for name in names:
#             proto = copy.deepcopy(self.proto)
#             proto.parameters.name = name
#             expected_cache_names.append(name)
#             self.cache = self.logic.update_cache(self.envelope,
#                                                               self.proto,
#                                                               self.cache)

#             cache_names = [x.parameters.name for x in
#                            self.cache[self.envelope]]
#             for cn in expected_cache_names:
#                 assert cn in cache_names

#         names = ['1', '2', '3', '4', '5']
#         expected_cache_names = []
#         for name in names:
#             proto = copy.deepcopy(self.proto)
#             proto.parameters.name = name
#             expected_cache_names.append(name)
#             self.cache = self.logic.update_cache(self.envelope,
#                                                               self.proto,
#                                                               self.cache)

#             cache_names = [x.parameters.name for x in
#                            self.cache[self.envelope]]
#             for cn in expected_cache_names:
#                 assert cn in cache_names


# class TestPBCWithROILogic(TestPBCLogicLVC):
#     """Test Proto-Based Caching with Special ROI Logic.

#     We cache Scan2ds based on their physical sizes.
#     """

#     def __init__(self):
#         self.logic = cl.PBCWithROILogic()  # LVC is default

#     def test_update_cache(self):
#         super().test_update_cache()

#         # Since we have cache keys for different sized Scand2ds, the first
#         # scan should still be there.
#         assert self.proto in self.cache[self.envelope]
