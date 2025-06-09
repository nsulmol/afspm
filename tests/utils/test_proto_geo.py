"""Test proto_geo logic."""

import logging
import pytest

from afspm.io import common
from afspm.utils import proto_geo
from afspm.io.protos.generated import scan_pb2

logger = logging.getLogger(__name__)

# TODO: Uncomment these fixture parametrizations when rotation angles are
# supported.
#@pytest.fixture(params=[None, 30.0])
@pytest.fixture
def scan1(request):
    scan_params = common.create_scan_params_2d(top_left=[5.0, 10.0],
                                               size=[10, 20],
                                               data_shape=[32, 32])
#                                               angle=request.param)
    scan = scan_pb2.Scan2d(params=scan_params)
    return scan


#@pytest.fixture(params=[None, 30.0])
@pytest.fixture
def scan2(request):  # Should intersect with scan1
    scan_params = common.create_scan_params_2d(top_left=[10.0, 20.0],
                                               size=[10, 20],
                                               data_shape=[48, 48])
#                                               angle=request.param)
    scan = scan_pb2.Scan2d(params=scan_params)
    return scan


#@pytest.fixture(params=[None, 30.0])
@pytest.fixture
def scan3(request):  # Should not intersect with scan1
    scan_params = common.create_scan_params_2d(top_left=[15.0, 30.0],
                                               size=[10, 20],
                                               data_shape=[32, 32])
#                                               angle=request.param)
    scan = scan_pb2.Scan2d(params=scan_params)
    return scan


@pytest.fixture
def min_intersection_ratio():
    return 0.25


@pytest.fixture
def min_spatial_res_ratio():
    return 0.25


def test_get_latest_intersection(scan1, scan2, scan3, min_intersection_ratio,
                                 min_spatial_res_ratio):
    logger.info("Validate that our spatial intersection logic works.")

    logger.debug("A case with intersection.")
    inter_scan = proto_geo.get_latest_intersection([scan1], scan2,
                                                   min_intersection_ratio,
                                                   min_spatial_res_ratio)
    logger.warning(f'inter_scan: {inter_scan}')
    assert inter_scan == scan1

    logger.debug("A case without intersection.")
    inter_scan = proto_geo.get_latest_intersection([scan1], scan3,
                                                   min_intersection_ratio,
                                                   min_spatial_res_ratio)
    assert inter_scan is None

    logger.debug("A too-high intersection ratio should give no intersection.")
    inter_scan = proto_geo.get_latest_intersection([scan1], scan2,
                                                   0.99,
                                                   min_spatial_res_ratio)
    assert inter_scan is None

    logger.debug("A too-low spatial resolution ratio should give no "
                 "intersection.")
    inter_scan = proto_geo.get_latest_intersection([scan1], scan2,
                                                   min_intersection_ratio,
                                                   0.75)
    assert inter_scan is None

    # TODO: Uncomment if you uncomment this logic in rect_intersection
    # logger.debug("Any rotation provided throws an exception (for now).")
    # scan2.params.spatial.roi.angle = 30.0
    # with pytest.raises(ValueError):
    #     proto_geo.get_latest_intersection([scan1], scan2,
    #                                       min_intersection_ratio,
    #                                       min_spatial_res_ratio)
