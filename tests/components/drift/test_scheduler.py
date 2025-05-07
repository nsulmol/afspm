"""Test DriftCorrectedScheduler logic."""

import logging
from pathlib import Path
from os import sep
import pytest
import datetime as dt
import numpy as np
import xarray as xr
from collections import deque
import zmq
from google.protobuf.timestamp_pb2 import Timestamp

from afspm.components.drift import scheduler
from afspm.components.drift import drift
from afspm.io import common
from afspm.io.pubsub import cache as pbc
from afspm.io.control import router as ctrl_rtr
from afspm.io.protos.generated import scan_pb2
from afspm.io.protos.generated import geometry_pb2
from afspm.utils import csv

import SciFiReaders as sr
from afspm.utils import array_converters as conv


logger = logging.getLogger(__name__)


@pytest.fixture(autouse=True)
def units():
    return 'nm'


@pytest.fixture(autouse=True)
def dt1():
    return dt.datetime(2025, 1, 1)


@pytest.fixture(autouse=True)
def dt2():
    return dt.datetime(2025, 1, 1, second=1)


@pytest.fixture(autouse=True)
def dt3():
    return dt.datetime(2025, 1, 1, second=2)


@pytest.fixture(autouse=True)
def vec1():
    return np.array([0.5, 0.5])


@pytest.fixture(autouse=True)
def vec2(vec1):
    return -vec1


@pytest.fixture(autouse=True)
def vec3():
    return np.array([1.0, 0])


@pytest.fixture
def correction_infos_cancel_out(dt1, dt2, dt3, vec1, vec2, units):
    info1 = scheduler.CorrectionInfo(dt1, dt2, vec1, units)
    info2 = scheduler.CorrectionInfo(dt2, dt3, vec2, units)
    infos = deque(maxlen=2)
    infos.append(info1)
    infos.append(info2)
    return infos


@pytest.fixture
def correction_infos_no_cancel(dt1, dt2, dt3, vec1, vec3, units):
    info1 = scheduler.CorrectionInfo(dt1, dt2, vec1, units)
    info2 = scheduler.CorrectionInfo(dt2, dt3, vec3, units)
    infos = deque(maxlen=2)
    infos.append(info1)
    infos.append(info2)
    return infos


@pytest.fixture
def csv_attribs():
    return csv.CSVAttributes('/tmp/test_scheduler.csv')


@pytest.fixture
def ctx():
    return zmq.Context.instance()


@pytest.fixture(scope="module")
def pub_url():
    return "tcp://127.0.0.1:1110"


@pytest.fixture(scope="module")
def psc_url():
    return "tcp://127.0.0.1:1111"


@pytest.fixture(scope="module")
def server_url():
    return "tcp://127.0.0.1:1112"


@pytest.fixture(scope="module")
def router_url():
    return "tcp://127.0.0.1:1113"


# NOTE: I am not using fixtures here because they appeared to cause issues
# with my monkeypatching of the corrected_scheduler. Additionally, the child
# router/cache created by CSCorrectedScheduler has the same...everything as
# the parent, so the fixtures lasting the lifetime of the test *could*  in
# theory cause issues.
def create_cache(pub_url, psc_url, ctx):
    return pbc.PubSubCache(pub_url, psc_url, ctx=ctx)


def create_router(server_url, router_url, ctx):
    return ctrl_rtr.ControlRouter(server_url, router_url, ctx)


def create_scheduler(cache, router, csv_attribs):
    return scheduler.CSCorrectedScheduler(csv_attribs=csv_attribs,
                                          name='scheduler',
                                          pubsubcache=cache,
                                          router=router)


def create_scheduler_and_ios(pub_url, psc_url, server_url, router_url, ctx,
                             csv_attribs):
    my_cache = create_cache(pub_url, psc_url, ctx)
    my_router = create_router(server_url, router_url, ctx)
    my_scheduler = create_scheduler(my_cache, my_router, csv_attribs)
    return my_scheduler


def send_empty_scan(self):
    """For monkeypatching into the cache."""
    ts = Timestamp()
    ts.FromDatetime(dt.datetime(2025, 1, 1, second=3))
    scan = scan_pb2.Scan2d(timestamp=ts)
    self.send_message(scan)
    self.scan_was_received = True


def fake_update_scheduler(self, proto):
    """Update the correction vector for the scheduler."""
    self.current_correction_vec = np.array([1.0, 0.5])
    self._update_io()


def test_hooks_work(pub_url, psc_url, server_url, router_url, ctx, csv_attribs,
                    monkeypatch):
    logger.info("Validate that the hooks between scheduler, router, cache all "
                "work.")
    monkeypatch.setattr(pbc.PubSubCache, 'poll', send_empty_scan)
    monkeypatch.setattr(scheduler.CSCorrectedScheduler, 'update',
                        fake_update_scheduler)
    my_scheduler = create_scheduler_and_ios(pub_url, psc_url, server_url,
                                            router_url, ctx, csv_attribs)

    # Before running, there should be no 'scan_was_received' in cache
    assert not hasattr(my_scheduler.pubsubcache, 'scan_was_received')

    my_scheduler.run_per_loop()

    expected_correction_vec = np.array([1.0, 0.5])
    assert hasattr(my_scheduler.pubsubcache, 'scan_was_received')

    assert np.all(np.isclose(my_scheduler.current_correction_vec,
                             expected_correction_vec))
    assert np.all(np.isclose(my_scheduler.pubsubcache._correction_vec,
                             expected_correction_vec))
    assert np.all(np.isclose(my_scheduler.router._correction_vec,
                             -expected_correction_vec))

    # Kill context (needed due to funky lack of pytest fixture)
    ctx.destroy()


def fake_get_latest_intersection(scans: list[scan_pb2.Scan2d],
                                 new_scan: scan_pb2.Scan2d,
                                 min_intersection_ratio: float,
                                 min_spatial_res_ratio: float,
                                 ) -> scan_pb2.Scan2d | None:
    return scan_pb2.Scan2d()


def fake_get_no_intersection(scans: list[scan_pb2.Scan2d],
                             new_scan: scan_pb2.Scan2d,
                             min_intersection_ratio: float,
                             min_spatial_res_ratio: float,
                             ) -> scan_pb2.Scan2d | None:
    return None


def fake_compute_correction_info(scan1: scan_pb2.Scan2d,
                                 scan2: scan_pb2.Scan2d,
                                 drift_model: drift.DriftModel
                                 ) -> scheduler.CorrectionInfo:
    vec = np.array([1.0, 0.5])
    return scheduler.CorrectionInfo(dt1, dt2, vec, units)


def test_correction_vec_with_match(pub_url, psc_url, server_url, router_url,
                                   ctx, csv_attribs,  monkeypatch):
    logger.info("Validate if a match is found, the internal logic works as "
                "expected.")
    monkeypatch.setattr(pbc.PubSubCache, 'poll', send_empty_scan)
    monkeypatch.setattr(scheduler, 'get_latest_intersection',
                        fake_get_latest_intersection)
    monkeypatch.setattr(scheduler, 'compute_correction_info',
                        fake_compute_correction_info)

    my_scheduler = create_scheduler_and_ios(pub_url, psc_url, server_url,
                                            router_url, ctx, csv_attribs)
    my_scheduler.run_per_loop()
    expected_correction_vec = np.array([1.0, 0.5])
    assert np.all(np.isclose(my_scheduler.current_correction_vec,
                             expected_correction_vec))

    # Kill context (needed due to funky lack of pytest fixture)
    ctx.destroy()


def test_correction_vec_no_match(pub_url, psc_url, server_url, router_url,
                                 ctx, csv_attribs,  monkeypatch,
                                 correction_infos_cancel_out,
                                 correction_infos_no_cancel):
    logger.info("Validate if a match is *not* found, the internal logic works "
                "as expected.")

    monkeypatch.setattr(pbc.PubSubCache, 'poll', send_empty_scan)
    monkeypatch.setattr(scheduler, 'get_latest_intersection',
                        fake_get_no_intersection)

    my_scheduler = create_scheduler_and_ios(pub_url, psc_url, server_url,
                                            router_url, ctx, csv_attribs)

    logger.debug("Validate it doesn't crash if we have no correction infos. ")
    my_scheduler.run_per_loop()
    expected_correction_vec = np.array([0.0, 0.0])
    assert np.all(np.isclose(my_scheduler.current_correction_vec,
                             expected_correction_vec))

    logger.debug("Set correction_infos to vals that cancel out.")
    my_scheduler.correction_infos = correction_infos_cancel_out
    my_scheduler.run_per_loop()
    expected_correction_vec = np.array([0.0, 0.0])
    assert np.all(np.isclose(my_scheduler.current_correction_vec,
                             expected_correction_vec))

    logger.debug("Set correction_infos to vals that do not.")
    my_scheduler.correction_infos = correction_infos_no_cancel
    my_scheduler.run_per_loop()
    expected_correction_vec = np.array([0.75, 0.25])
    assert np.all(np.isclose(my_scheduler.current_correction_vec,
                             expected_correction_vec))

    # Kill context (needed due to funky lack of pytest fixture)
    ctx.destroy()


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
    inter_scan = scheduler.get_latest_intersection([scan1], scan2,
                                                   min_intersection_ratio,
                                                   min_spatial_res_ratio)
    logger.warning(f'inter_scan: {inter_scan}')
    assert inter_scan == scan1

    logger.debug("A case without intersection.")
    inter_scan = scheduler.get_latest_intersection([scan1], scan3,
                                                   min_intersection_ratio,
                                                   min_spatial_res_ratio)
    assert inter_scan is None

    logger.debug("A too-high intersection ratio should give no intersection.")
    inter_scan = scheduler.get_latest_intersection([scan1], scan2,
                                                   0.99,
                                                   min_spatial_res_ratio)
    assert inter_scan is None

    logger.debug("A too-low spatial resolution ratio should give no "
                 "intersection.")
    inter_scan = scheduler.get_latest_intersection([scan1], scan2,
                                                   min_intersection_ratio,
                                                   0.75)
    assert inter_scan is None

    logger.debug("Any rotation provided throws an exception (for now).")
    scan2.params.spatial.roi.angle = 30.0
    with pytest.raises(ValueError):
        scheduler.get_latest_intersection([scan1], scan2,
                                          min_intersection_ratio,
                                          min_spatial_res_ratio)

def get_xarray_from_ibw(fname: str) -> xr.DataArray:
    """Helper to get xarray from an ibw file."""
    reader = sr.IgorIBWReader(fname)
    ds1 = list(reader.read(verbose=False).values())
    scan1 = conv.convert_sidpy_to_scan_pb2(ds1[0])
    da1 = conv.convert_scan_pb2_to_xarray(scan1)
    return da1


BASE_PATH = str(Path(__file__).parent.parent.resolve())


@pytest.fixture
def sample_fname():
    return BASE_PATH + sep + '..' + sep + 'data' + sep + 'Au_facetcontac0000.ibw'


def create_rect_for_da(da: xr.DataArray) -> geometry_pb2.Rect2d:
    tl = geometry_pb2.Point2d(x=da.x[0], y=da.y[0])
    size = geometry_pb2.Size2d(x=da.x[-1] - da.x[0],
                               y=da.y[-1] - da.y[0])
    rect = geometry_pb2.Rect2d(top_left=tl, size=size)
    return rect


def test_extract_patch(sample_fname):
    da = get_xarray_from_ibw(sample_fname)
    rect = create_rect_for_da(da)

    # Create expanded array with NaN outside.
    new_tl = [da.x[0] - (da.x[-1] - da.x[0]), da.y[0] - (da.y[-1] - da.y[0])]
    x2 = np.linspace(new_tl[0], 2*da.x[-1], 4*da.x.shape[0])
    y2 = np.linspace(new_tl[1], 2*da.y[-1], 4*da.y.shape[0])
    da2 = da.interp(x=x2, y=y2)

    logger.debug('We should get da if we extract its region from da2.')
    extract_da = scheduler.extract_patch(da2, rect)
    assert np.all(da == extract_da)

    logger.debug('We should get all nan if we extract a region not from da.')
    logger.warning(f'rect before: {rect}')
    new_tl = geometry_pb2.Point2d(x=new_tl[0], y=new_tl[1])
    # not including the last value, to ensure we have all nan.
    size = geometry_pb2.Size2d(x=da.x[-2] - da.x[0],
                               y=da.y[-2] - da.y[0])
    rect = geometry_pb2.Rect2d(top_left=new_tl, size=size)

    extract_da = scheduler.extract_patch(da2, rect)
    assert np.isnan(extract_da).all()


def test_extract_and_scale_patches(sample_fname):
    da = get_xarray_from_ibw(sample_fname)
    rect = create_rect_for_da(da)

    # da2 is 2x the resolution in each dimension
    x2 = np.linspace(da.x[0], da.x[-1], 2*da.x.shape[0])
    y2 = np.linspace(da.y[0], da.y[-1], 2*da.y.shape[0])
    da2 = da.interp(x=x2, y=y2)

    logger.debug('If we try to extract and scale (da, da2) we expect '
                 '(da2, da2).')
    res_da1, res_da2 = scheduler.extract_and_scale_patches(da, da2, rect)
    assert np.all(np.isclose(res_da1, da2))
    assert np.all(np.isclose(res_da2, da2))

    # If we try to extract and scale (da2, da), we expect (da, da)
    logger.debug('If we try to extract and scale (da2, da) we expect '
                 '(da, da).')
    res_da1, res_da2 = scheduler.extract_and_scale_patches(da2, da, rect)
    assert np.all(np.isclose(res_da1, da))
    assert np.all(np.isclose(res_da2, da))

# NOTE: not testing compute_correction_info(), as it is basically a wrapper
# for drift's methods.
