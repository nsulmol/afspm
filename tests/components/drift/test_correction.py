"""Test drift correction logic."""

import logging
import pytest
from pathlib import Path
import datetime as dt
from os import sep
import numpy as np
import xarray as xr

from afspm.io.protos.generated import geometry_pb2
from afspm.components.drift import correction
from afspm.components.drift import drift
from afspm.utils import array_converters as ac


logger = logging.getLogger(__name__)


BASE_PATH = str(Path(__file__).parent.parent.resolve())


@pytest.fixture
def sample1_fname():
    return BASE_PATH + sep + '..' + sep + 'data' + sep + 'Au_facetcontac0000.nc'


@pytest.fixture
def sample2_fname():
    return BASE_PATH + sep + '..' + sep + 'data' + sep + 'Au_facetcontac0003.nc'


def get_data_array_from_dataset(fname: str) -> xr.DataArray:
    """Helper to get xarray DataArray from an nc file.

    The file contains a Dataset. We grab the first 'channel', i.e. the first
    DataArray.
    """
    ds = xr.open_dataset(fname)
    da = list(ds.values())[0]  # Grab first DataArray from Dataset
    return da


def create_rect_for_da(da: xr.DataArray) -> geometry_pb2.Rect2d:
    tl = geometry_pb2.Point2d(x=da.x[0], y=da.y[0])
    size = geometry_pb2.Size2d(x=da.x[-1] - da.x[0],
                               y=da.y[-1] - da.y[0])
    rect = geometry_pb2.Rect2d(top_left=tl, size=size)
    return rect


def test_extract_patch(sample1_fname):
    da = get_data_array_from_dataset(sample1_fname)
    rect = create_rect_for_da(da)

    # Create expanded array with NaN outside.
    new_tl = [da.x[0] - (da.x[-1] - da.x[0]), da.y[0] - (da.y[-1] - da.y[0])]
    x2 = np.linspace(new_tl[0], 2*da.x[-1], 4*da.x.shape[0])
    y2 = np.linspace(new_tl[1], 2*da.y[-1], 4*da.y.shape[0])
    da2 = da.interp(x=x2, y=y2)

    logger.debug('We should get da if we extract its region from da2.')
    extract_da = correction.extract_patch(da2, rect)
    assert np.all(da == extract_da)

    logger.debug('We should get all nan if we extract a region not from da.')
    new_tl = geometry_pb2.Point2d(x=new_tl[0], y=new_tl[1])
    # not including the last value, to ensure we have all nan.
    size = geometry_pb2.Size2d(x=da.x[-2] - da.x[0],
                               y=da.y[-2] - da.y[0])
    rect = geometry_pb2.Rect2d(top_left=new_tl, size=size)

    extract_da = correction.extract_patch(da2, rect)
    assert np.isnan(extract_da).all()


def test_extract_and_scale_patches(sample1_fname):
    da = get_data_array_from_dataset(sample1_fname)
    rect = create_rect_for_da(da)

    # da2 is 2x the resolution in each dimension
    x2 = np.linspace(da.x[0], da.x[-1], 2*da.x.shape[0])
    y2 = np.linspace(da.y[0], da.y[-1], 2*da.y.shape[0])
    da2 = da.interp(x=x2, y=y2)

    logger.debug('If we try to extract and scale (da, da2) we expect '
                 '(da2, da2).')
    res_da1, res_da2, score = correction.extract_and_scale_patches(da, da2,
                                                                   rect)
    assert np.all(np.isclose(res_da1, da2))
    assert np.all(np.isclose(res_da2, da2))
    assert np.all(np.isclose(score, np.array([1.0, 1.0])))

    logger.debug('If we try to extract and scale (da2, da) we *also* expect '
                 '(da2, da2).')
    res_da1, res_da2, score = correction.extract_and_scale_patches(da2, da,
                                                                   rect)
    assert np.all(np.isclose(res_da1, da2))
    assert np.all(np.isclose(res_da2, da2))
    assert np.all(np.isclose(score, np.array([2.0, 2.0])))

# NOTE: not testing compute_correction_info(), as it is basically a wrapper
# for drift's methods.


@pytest.fixture
def good_max_score():
    return 0.007


@pytest.fixture
def bad_max_score():
    return 0.005


@pytest.fixture
def expected_snapshot():
    return correction.DriftSnapshot(
        dt.datetime(1970, 1, 1, tzinfo=dt.timezone.utc),
        dt.datetime(1970, 1, 1, tzinfo=dt.timezone.utc),
        np.array([-2.65830923e-07, -4.33944069e-08]),
        'm')


def test_compute_drift_snapshot(sample1_fname, sample2_fname,
                                good_max_score, bad_max_score,
                                expected_snapshot):
    logger.info("Ensure compute_drift_snapshot works as expected.")
    da1 = get_data_array_from_dataset(sample1_fname)
    da2 = get_data_array_from_dataset(sample2_fname)

    scan1 = ac.convert_xarray_to_scan_pb2(da1)
    scan2 = ac.convert_xarray_to_scan_pb2(da2)

    drift_model = drift.create_drift_model()

    logger.info("Validate if we feed a proper max score, we get an expected "
                "DriftSnapshot.")
    snapshot = correction.compute_drift_snapshot(scan1, scan2, drift_model,
                                                 good_max_score)
    # Validate the snapshot is what we expect
    assert snapshot.dt1 == expected_snapshot.dt1
    assert snapshot.dt2 == expected_snapshot.dt2
    assert snapshot.dt2 == expected_snapshot.dt2
    assert np.all(np.isclose(snapshot.vec, expected_snapshot.vec))
    assert snapshot.unit == expected_snapshot.unit

    # Validate if we feed a too small max score, we get nothing.
    logger.info("Validate if we feed a too-small max score, we get nothing.")
    snapshot = correction.compute_drift_snapshot(scan1, scan2, drift_model,
                                                 bad_max_score)
    assert snapshot is None


@pytest.fixture
def test_vec():
    return np.array([1.0, 0.0])


@pytest.fixture
def dt1():
    return dt.datetime(1970, 1, 1, tzinfo=dt.timezone.utc)


@pytest.fixture
def dt2():  # 1 second longer than dt1
    return dt.datetime(1970, 1, 1, 0, 0, 1, tzinfo=dt.timezone.utc)


def test_get_drift_rate(test_vec, dt1, dt2):
    start_dts = [dt1, None, None]
    end_dts = [None, dt2, None]

    logger.info("Assert if we feed one or both dts as None, we get NO_VEC.")
    for start_dt, end_dt in zip(start_dts, end_dts):
        ret_vec = correction.get_drift_rate(test_vec, start_dt, end_dt)
        assert np.all(np.isclose(ret_vec, correction.NO_VEC))

    logger.info("Assert if we feed reasonable values we get a reasonable "
                "drift rate.")
    ret_vec = correction.get_drift_rate(test_vec, dt1, dt2)
    assert np.all(np.isclose(ret_vec, test_vec))  # test_vec / 1 is test_vec

    logger.info("Assert we get NO_VEC for zero division.")
    ret_vec = correction.get_drift_rate(test_vec, dt1, dt1)
    assert np.all(np.isclose(ret_vec, correction.NO_VEC))


def test_estimate_correction_vec(test_vec, dt1, dt2):
    drift_rate = correction.get_drift_rate(test_vec, dt1, dt2)

    start_dts = [dt1, None, None]
    end_dts = [None, dt2, None]

    logger.info("Assert if we feed one or both dts as None, we get NO_VEC.")
    for start_dt, end_dt in zip(start_dts, end_dts):
        ret_vec = correction.estimate_correction_vec(drift_rate, start_dt,
                                                     end_dt)
        assert np.all(np.isclose(ret_vec, correction.NO_VEC))

    ret_vec = correction.estimate_correction_vec(drift_rate, dt1, dt2)
    assert np.all(np.isclose(ret_vec, test_vec))  # vec -> rate -> vec


@pytest.fixture
def drift_rate():
    return np.array([0.1, 0.0])


@pytest.fixture
def unit():
    return 'm'


def test_estimate_correction_no_snapshot(test_vec, drift_rate, dt1, dt2,
                                         unit):
    logger.info("If we provided None, we get None.")
    updated_info = correction.estimate_correction_no_snapshot(None, dt2)
    assert updated_info is None

    logger.info("No drift means CorrectionInfo is all 0s")
    corr_info = correction.CorrectionInfo(dt1, test_vec, correction.NO_VEC,
                                          unit)
    updated_info = correction.estimate_correction_no_snapshot(corr_info, dt2)
    exp_corr_info = correction.CorrectionInfo(dt2, correction.NO_VEC,
                                              correction.NO_VEC,
                                              corr_info.unit)
    assert updated_info == exp_corr_info

    logger.info("The drift runs as expected.")
    corr_info = correction.CorrectionInfo(dt1, test_vec, drift_rate,
                                          unit)
    updated_info = correction.estimate_correction_no_snapshot(corr_info, dt2)
    exp_corr_info = correction.CorrectionInfo(dt2, drift_rate, drift_rate,
                                              corr_info.unit)
    assert updated_info == exp_corr_info


@pytest.fixture
def dt_between():  # 0.25 s
    return dt.datetime(1970, 1, 1, 0, 0, 0, 250000, tzinfo=dt.timezone.utc)


@pytest.fixture
def old_corr_info(dt1, drift_rate, unit):
    return correction.CorrectionInfo(dt1, np.array([0.75, 0.0]),
                                     drift_rate, unit)


@pytest.fixture
def expected_corr_info(dt2, unit):
    # with 1.0 nm / s drift rate over 0.25 s overlap, we get 0.25 nm,
    # which we subtract from the snapshot vec to get 0.75 nm.
    # The assumed vec, given 0.1 nm / s over 0.25 s overlap, is 0.025 nm.
    # The actual vec then becomes 0.85 nm + 0.025 nm = 0.825 nm.
    vec = np.array([0.825, 0.0])
    # The rate is computed as 0.825 nm / (1 s - 0.25 s) = 1.1 nm
    rate = np.array([1.1, 0.0])
    return correction.CorrectionInfo(dt2, vec, rate, unit)


def test_estimate_correction_from_snapshot(test_vec, dt1, dt2, dt_between,
                                           unit, old_corr_info,
                                           expected_corr_info):
    snapshot = correction.DriftSnapshot(dt1, dt2, test_vec, unit)

    logger.info('Test only drift snapshot provided.')
    corr_info = correction.estimate_correction_from_snapshot(snapshot, None)
    exp_corr_info = correction.CorrectionInfo(snapshot.dt2, snapshot.vec,
                                              snapshot.vec, snapshot.unit)
    assert corr_info == exp_corr_info

    logger.info('Test a case with no time overlap.')
    corr_info = correction.estimate_correction_from_snapshot(snapshot,
                                                             old_corr_info)
    expected_vec = old_corr_info.drift_rate + test_vec
    exp_corr_info = correction.CorrectionInfo(snapshot.dt2, expected_vec,
                                              expected_vec, snapshot.unit)
    assert corr_info == exp_corr_info

    logger.info('Test a case *with* time overlap.')
    old_corr_info.curr_dt = dt_between
    corr_info = correction.estimate_correction_from_snapshot(snapshot,
                                                             old_corr_info)
    assert corr_info == expected_corr_info


@pytest.fixture
def new_corr_info(dt2, test_vec, unit):
    return correction.CorrectionInfo(dt2, test_vec, test_vec, unit)


@pytest.fixture
def update_weight():
    return 0.9


@pytest.fixture
def exp_corr_info_tot_corr(dt2, unit):
    return correction.CorrectionInfo(dt2, np.array([1.66, 0.0]),
                                     np.array([0.91, 0.0]), unit)


def test_update_total_correction(old_corr_info, new_corr_info, dt1, dt2,
                                 update_weight, exp_corr_info_tot_corr):
    logger.info('Confirm if no new corr_info provided, we return None.')
    res_corr_info = correction.update_total_correction(old_corr_info, None,
                                                       update_weight)
    assert res_corr_info is None

    logger.info('Confirm if no old corr_info provided, we return new.')
    res_corr_info = correction.update_total_correction(None, new_corr_info,
                                                       update_weight)
    assert res_corr_info == new_corr_info

    logger.info('Confirm it functions as expected for old and new corr_infos.')
    res_corr_info = correction.update_total_correction(old_corr_info,
                                                       new_corr_info,
                                                       update_weight)
    assert res_corr_info == exp_corr_info_tot_corr
