"""Test DriftCorrectedScheduler logic."""

import logging
import pytest
import datetime as dt
import numpy as np
import copy
import zmq
from google.protobuf.timestamp_pb2 import Timestamp

from afspm.components.drift import scheduler
from afspm.components.drift import correction
from afspm.components.drift import drift
from afspm.io.pubsub import cache as pbc
from afspm.io.control import router as ctrl_rtr
from afspm.io.protos.generated import scan_pb2
from afspm.utils import csv


logger = logging.getLogger(__name__)


@pytest.fixture(autouse=True)
def unit():
    return 'nm'


@pytest.fixture(autouse=True)
def dt1():
    return dt.datetime(2025, 1, 1, tzinfo=dt.timezone.utc)


@pytest.fixture(autouse=True)
def dt2():
    return dt.datetime(2025, 1, 1, second=1, tzinfo=dt.timezone.utc)


@pytest.fixture(autouse=True)
def dt3():
    return dt.datetime(2025, 1, 1, second=2, tzinfo=dt.timezone.utc)


@pytest.fixture(autouse=True)
def vec1():
    return np.array([0.5, 0.5])


@pytest.fixture(autouse=True)
def vec2(vec1):
    return -vec1


@pytest.fixture(autouse=True)
def vec3():
    return np.array([1.0, 0])


# @pytest.fixture
# def correction_infos_cancel_out(dt1, dt2, dt3, vec1, vec2, units):
#     info1 = correction.CorrectionInfo(dt1, dt2, vec1, units)
#     info2 = correction.CorrectionInfo(dt2, dt3, vec2, units)
#     infos = deque(maxlen=2)
#     infos.append(info1)
#     infos.append(info2)
#     return infos


# @pytest.fixture
# def correction_infos_no_cancel(dt1, dt2, dt3, vec1, vec3, units):
#     info1 = correction.CorrectionInfo(dt1, dt2, vec1, units)
#     info2 = correction.CorrectionInfo(dt2, dt3, vec3, units)
#     infos = deque(maxlen=2)
#     infos.append(info1)
#     infos.append(info2)
#     return infos


@pytest.fixture
def channel_id():
    return 'Topo'


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


def create_scheduler(cache, router, csv_attribs, channel_id):
    return scheduler.CSCorrectedScheduler(channel_id=channel_id,
                                          csv_attribs=csv_attribs,
                                          name='scheduler',
                                          pubsubcache=cache,
                                          router=router,
                                          display_fit=False)


def create_scheduler_and_ios(pub_url, psc_url, server_url, router_url, ctx,
                             csv_attribs, channel_id):
    my_cache = create_cache(pub_url, psc_url, ctx)
    my_router = create_router(server_url, router_url, ctx)
    my_scheduler = create_scheduler(my_cache, my_router, csv_attribs, channel_id)
    return my_scheduler


def send_empty_scan(self):
    """For monkeypatching into the cache."""
    ts = Timestamp()
    ts.FromDatetime(dt.datetime(2025, 1, 1, second=3))
    scan = scan_pb2.Scan2d(timestamp=ts)
    self.send_message(scan)
    self.scan_was_received = True


@pytest.fixture
def corr_info(dt1, vec1, unit):
    return correction.CorrectionInfo(dt1, vec1, vec1, unit)

# manually feeding corr_info instead of proto
def fake_update_scheduler(self, corr_info):
    """Update the correction vector for the scheduler."""
    logger.warning('in update!')
    self.total_corr_info = corr_info
    self._update_io()


# TODO: uncomment me!
# def test_hooks_work(pub_url, psc_url, server_url, router_url, ctx, csv_attribs,
#                     channel_id, corr_info, monkeypatch):
#     logger.info("Validate that the hooks between scheduler, router, cache all "
#                 "work.")
#     monkeypatch.setattr(pbc.PubSubCache, 'poll', send_empty_scan)
#     monkeypatch.setattr(scheduler.CSCorrectedScheduler, 'update',
#                         fake_update_scheduler)
#     my_scheduler = create_scheduler_and_ios(pub_url, psc_url, server_url,
#                                             router_url, ctx, csv_attribs,
#                                             channel_id)

#     # Before running, there should be no 'scan_was_received' in cache
#     assert not hasattr(my_scheduler.pubsubcache, 'scan_was_received')

#     logger.warning(f'corr_info: {corr_info}')
#     my_scheduler.update(corr_info)
#     my_scheduler.run_per_loop()

#     assert hasattr(my_scheduler.pubsubcache, 'scan_was_received')
#     assert my_scheduler.total_corr_info == corr_info
#     assert my_scheduler.pubsubcache._corr_info == corr_info

#     # neg_corr_info = copy.deepcopy(corr_info)
#     # neg_corr_info.vec = -neg_corr_info.vec
#     # neg_corr_info.drift_rate = -neg_corr_info.drift_rate
#     # assert my_scheduler.router._corr_info == neg_corr_info

#     # Kill context (needed due to funky lack of pytest fixture)
#     ctx.destroy()


# TODO: Consider removing/updating!
# def fake_get_latest_intersection(scans: list[scan_pb2.Scan2d],
#                                  new_scan: scan_pb2.Scan2d,
#                                  min_intersection_ratio: float,
#                                  min_spatial_res_ratio: float,
#                                  ) -> scan_pb2.Scan2d | None:
#     return scan_pb2.Scan2d()


# def fake_get_no_intersection(scans: list[scan_pb2.Scan2d],
#                              new_scan: scan_pb2.Scan2d,
#                              min_intersection_ratio: float,
#                              min_spatial_res_ratio: float,
#                              ) -> scan_pb2.Scan2d | None:
#     return None


# def fake_compute_correction_info(scan1: scan_pb2.Scan2d,
#                                  scan2: scan_pb2.Scan2d,
#                                  drift_model: drift.DriftModel,
#                                  min_score: float,
#                                  ) -> correction.CorrectionInfo:
#     vec = np.array([1.0, 0.5])
#     return correction.CorrectionInfo(dt1, dt2, vec, unit)


# def test_correction_vec_with_match(pub_url, psc_url, server_url, router_url,
#                                    ctx, csv_attribs,  monkeypatch):
#     logger.info("Validate if a match is found, the internal logic works as "
#                 "expected.")
#     monkeypatch.setattr(pbc.PubSubCache, 'poll', send_empty_scan)
#     monkeypatch.setattr(correction, 'get_latest_intersection',
#                         fake_get_latest_intersection)
#     monkeypatch.setattr(scheduler, 'compute_correction_info',
#                         fake_compute_correction_info)

#     my_scheduler = create_scheduler_and_ios(pub_url, psc_url, server_url,
#                                             router_url, ctx, csv_attribs)
#     my_scheduler.run_per_loop()
#     expected_correction_vec = np.array([1.0, 0.5])
#     assert np.all(np.isclose(my_scheduler.current_correction_vec,
#                              expected_correction_vec))

#     # Kill context (needed due to funky lack of pytest fixture)
#     ctx.destroy()


# def test_correction_vec_no_match(pub_url, psc_url, server_url, router_url,
#                                  ctx, csv_attribs,  monkeypatch,
#                                  correction_infos_cancel_out,
#                                  correction_infos_no_cancel):
#     logger.info("Validate if a match is *not* found, the internal logic works "
#                 "as expected.")

#     monkeypatch.setattr(pbc.PubSubCache, 'poll', send_empty_scan)
#     monkeypatch.setattr(correction, 'get_latest_intersection',
#                         fake_get_no_intersection)

#     my_scheduler = create_scheduler_and_ios(pub_url, psc_url, server_url,
#                                             router_url, ctx, csv_attribs)

#     logger.debug("Validate it doesn't crash if we have no correction infos. ")
#     my_scheduler.run_per_loop()
#     expected_correction_vec = np.array([0.0, 0.0])
#     assert np.all(np.isclose(my_scheduler.current_correction_vec,
#                              expected_correction_vec))

#     logger.debug("Set correction_infos to vals that cancel out.")
#     my_scheduler.correction_infos = correction_infos_cancel_out
#     my_scheduler.run_per_loop()
#     expected_correction_vec = np.array([0.0, 0.0])
#     assert np.all(np.isclose(my_scheduler.current_correction_vec,
#                              expected_correction_vec))

#     logger.debug("Set correction_infos to vals that do not.")
#     my_scheduler.correction_infos = correction_infos_no_cancel
#     my_scheduler.run_per_loop()
#     expected_correction_vec = np.array([0.75, 0.25])
#     assert np.all(np.isclose(my_scheduler.current_correction_vec,
#                              expected_correction_vec))

#     # Kill context (needed due to funky lack of pytest fixture)
#     ctx.destroy()


# def no_compute_correction_info(scan1: scan_pb2.Scan2d,
#                                scan2: scan_pb2.Scan2d,
#                                drift_model: drift.DriftModel,
#                                min_score: float,
#                                ) -> correction.CorrectionInfo:
#     return None


# def test_compute_correction_info_none(pub_url, psc_url, server_url, router_url,
#                                       ctx, csv_attribs,  monkeypatch):
#     logger.info("Validate if compute_correction_info returns None all is good.")

#     monkeypatch.setattr(pbc.PubSubCache, 'poll', send_empty_scan)
#     monkeypatch.setattr(scheduler, 'get_latest_intersection',
#                         fake_get_no_intersection)
#     monkeypatch.setattr(scheduler, 'compute_correction_info',
#                         no_compute_correction_info)

#     my_scheduler = create_scheduler_and_ios(pub_url, psc_url, server_url,
#                                             router_url, ctx, csv_attribs)

#     logger.debug("Validate it doesn't crash if we have no correction infos. ")
#     my_scheduler.run_per_loop()
#     expected_correction_vec = np.array([0.0, 0.0])
#     assert np.all(np.isclose(my_scheduler.current_correction_vec,
#                              expected_correction_vec))
