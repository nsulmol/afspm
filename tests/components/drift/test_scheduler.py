"""Test DriftCorrectedScheduler logic."""

import logging
import pytest
import datetime as dt
import numpy as np
from collections import deque
import zmq
from google.protobuf.timestamp_pb2 import Timestamp

from afspm.components.drift import scheduler
from afspm.components.drift import drift
from afspm.io.pubsub import cache as pbc
from afspm.io.control import router as ctrl_rtr
from afspm.io.protos.generated import scan_pb2
from afspm.utils import csv


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
                                 intersection_ratio: float
                                 ) -> scan_pb2.Scan2d | None:
    return scan_pb2.Scan2d()


def fake_get_no_intersection(scans: list[scan_pb2.Scan2d],
                             new_scan: scan_pb2.Scan2d,
                             intersection_ratio: float
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
    logger.debug("First, set correction_infos to vals that cancel out.")
    my_scheduler.correction_infos = correction_infos_cancel_out

    my_scheduler.run_per_loop()

    expected_correction_vec = np.array([0.0, 0.0])
    assert np.all(np.isclose(my_scheduler.current_correction_vec,
                             expected_correction_vec))

    logger.debug("Then, set correction_infos to vals that do not.")

    my_scheduler.correction_infos = correction_infos_no_cancel

    my_scheduler.run_per_loop()

    expected_correction_vec = np.array([0.75, 0.25])
    assert np.all(np.isclose(my_scheduler.current_correction_vec,
                             expected_correction_vec))

    # Kill context (needed due to funky lack of pytest fixture)
    ctx.destroy()
