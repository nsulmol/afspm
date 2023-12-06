"""Test experiment logic."""

import pytest
import logging
import numpy as np
from collections import deque

from samples.point_subscan import experiment
from afspm.io import common
from afspm.components import component
from afspm.io.pubsub import subscriber

from afspm.io.protos.generated import geometry_pb2
from afspm.io.protos.generated import analysis_pb2


logger = logging.getLogger(__name__)


@pytest.fixture
def bound_origin():
    return [0, 0]


@pytest.fixture
def center_step():
    return 10


@pytest.fixture
def bound_size():
    return [30, 30]


@pytest.fixture
def roi_def_center():
    return [15, 15]


@pytest.fixture
def roi_size():
    return [20, 20]


@pytest.fixture
def points_id():
    return "SpatialPointWithScoreList"


@pytest.mark.parametrize("roi_center, exp_origin",
                         [([15, 15], [5, 5]),
                          ([5, 15], [0, 5]),
                          ([15, 25], [5, 10])])
def test_get_roi_within_bounds_fix_size(bound_origin, bound_size,
                                        roi_center, roi_size,
                                        exp_origin):
    res_origin, res_size = experiment.get_roi_within_bounds_fix_size(
        np.array(bound_origin), np.array(bound_size),
        np.array(roi_center), np.array(roi_size))
    assert (res_origin == np.array(exp_origin)).all()
    assert (res_size == np.array(roi_size)).all()


@pytest.fixture
def center_pt():
    return [15, 15]


@pytest.fixture
def sub_scan_size():
    return [3, 3]


@pytest.fixture
def units():
    return 'nm'


@pytest.fixture
def sscans_per_fscan():
    return 4


@pytest.fixture
def sscan_res():
    return [1000, 1000]


@pytest.fixture
def exp_data(points_id, sub_scan_size, units,
             sscans_per_fscan, sscan_res):
    return experiment.ExperimentData(
        phys_units=units,
        full_scan_res=[600, 600],
        full_scan_phys_origin=[0, 0],
        full_scan_phys_size=[30, 30],
        data_units=units,
        sub_scan_res=sscan_res,
        sub_scan_phys_size=[sub_scan_size[0], sub_scan_size[1]],
        sub_scans_per_full_scan=sscans_per_fscan,
        points_id=points_id
    )


@pytest.fixture
def sample_points(center_pt):
    points_list = analysis_pb2.SpatialPointWithScoreList()
    pt2d = geometry_pb2.Point2d(x=center_pt[0], y=center_pt[1])
    spatial = analysis_pb2.SpatialPoint(point=pt2d)
    pt_with_score = analysis_pb2.SpatialPointWithScore(
        spatial=spatial)
    points_list.spatials.append(pt_with_score)
    return points_list


@pytest.fixture
def expected_sub_scan(center_pt, sub_scan_size, units, sscan_res):
    origin = (np.array(center_pt) - 0.5 * np.array(sub_scan_size)).tolist()
    return common.create_scan_params_2d(
        origin, sub_scan_size, units, sscan_res, units)


@pytest.fixture
def expected_full_scan(exp_data):
    return common.create_scan_params_2d(
        exp_data.full_scan_phys_origin,
        exp_data.full_scan_phys_size,
        exp_data.phys_units,
        exp_data.full_scan_res,
        exp_data.data_units)


@pytest.fixture
def sample_sub(sample_points, points_id):
    sub = subscriber.Subscriber("ipc://fake_url")
    sub.cache[points_id] = deque(maxlen=5)
    sub.cache[points_id].append(sample_points)
    return sub


@pytest.fixture
def sample_component(sample_sub):
    return component.AfspmComponentBase("test", sample_sub)


def test_get_next_scan_params(exp_data, sample_component,
                              sample_points, expected_full_scan,
                              expected_sub_scan):
    # Start with full scan
    next_scan = experiment.get_next_scan_params(sample_component, exp_data)
    assert next_scan == expected_full_scan

    # Validate expected # of subscans
    for __ in range(exp_data.sub_scans_per_full_scan):
        next_scan = experiment.get_next_scan_params(sample_component, exp_data)
        assert next_scan == expected_sub_scan
