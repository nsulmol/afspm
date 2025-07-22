"""Test DriftCorrectedScheduler logic."""

import logging
import pytest
import tempfile
import datetime as dt
import numpy as np
import copy
import zmq
from google.protobuf.timestamp_pb2 import Timestamp

from afspm.components.drift import scheduler
from afspm.components.drift import correction
from afspm.io.pubsub import publisher, subscriber, cache as pbc
from afspm.io.control import router as ctrl_rtr
from afspm.io.protos.generated import scan_pb2, geometry_pb2
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
def vec():
    return np.array([0.5, 0.5])


@pytest.fixture
def channel_id():
    return 'Topo'


@pytest.fixture
def csv_attribs():
    return csv.CSVAttributes(filepath=tempfile.gettempdir() +
                             '/test_scheduler.csv')


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


@pytest.fixture(scope="module")
def rescan_url():
    return "tcp://127.0.0.1:1114"


# NOTE: I am not using fixtures here because they appeared to cause issues
# with my monkeypatching of the corrected_scheduler. Additionally, the child
# router/cache created by CSCorrectedScheduler has the same...everything as
# the parent, so the fixtures lasting the lifetime of the test *could*  in
# theory cause issues.
def create_cache(pub_url, psc_url, ctx):
    return pbc.PubSubCache(pub_url, psc_url, ctx=ctx)


def create_router(server_url, router_url, ctx):
    return ctrl_rtr.ControlRouter(server_url, router_url, ctx)


def create_publisher(rescan_url, ctx):
    return publisher.Publisher(rescan_url, ctx=ctx)


def create_scheduler(cache, router, publisher, csv_attribs, channel_id):
    return scheduler.CSCorrectedScheduler(channel_id=channel_id,
                                          csv_attribs=csv_attribs,
                                          name='scheduler',
                                          pubsubcache=cache,
                                          router=router,
                                          display_fit=False,
                                          publisher=publisher)


def create_scheduler_and_ios(pub_url, psc_url, server_url, router_url,
                             rescan_url, ctx,
                             csv_attribs, channel_id, update_weight):
    my_cache = create_cache(pub_url, psc_url, ctx)
    my_router = create_router(server_url, router_url, ctx)
    my_publisher = create_publisher(rescan_url, ctx) if rescan_url else None

    my_scheduler = create_scheduler(my_cache, my_router, my_publisher,
                                    csv_attribs, channel_id)
    my_scheduler.update_weight = update_weight
    return my_scheduler


def send_empty_scan(self):
    """For monkeypatching into the cache."""
    ts = Timestamp()
    ts.FromDatetime(dt.datetime(2025, 1, 1, second=3))
    scan = scan_pb2.Scan2d(timestamp=ts)
    # We need length_units to have some unit, so we can do conversions
    # when updating the Scan2d/ScanParameters2d protos.
    scan.params.spatial.length_units = 'nm'
    self.send_message(scan)
    self.scan_was_received = True


@pytest.fixture
def corr_info(dt1, vec, unit):
    return correction.CorrectionInfo(dt1, vec, vec, unit)


@pytest.fixture
def update_weight():
    return 0.667


def fake_update_scheduler(self, corr_info):
    """Manually feed corr_info instead of proto."""
    logger.warning('in update!')
    self.total_corr_info = corr_info
    self._update_io()


def test_hooks_work(pub_url, psc_url, server_url, router_url, ctx, csv_attribs,
                    channel_id, corr_info, monkeypatch, update_weight):
    logger.info("Validate that the hooks between scheduler, router, cache all "
                "work.")
    monkeypatch.setattr(pbc.PubSubCache, 'poll', send_empty_scan)
    monkeypatch.setattr(scheduler.CSCorrectedScheduler, 'update',
                        fake_update_scheduler)
    my_scheduler = create_scheduler_and_ios(pub_url, psc_url, server_url,
                                            router_url, None, ctx, csv_attribs,
                                            channel_id, update_weight)

    # Before running, there should be no 'scan_was_received' in cache
    assert not hasattr(my_scheduler.pubsubcache, 'scan_was_received')

    logger.warning(f'corr_info: {corr_info}')
    my_scheduler.update(corr_info)
    my_scheduler.run_per_loop()

    assert hasattr(my_scheduler.pubsubcache, 'scan_was_received')
    assert my_scheduler.total_corr_info == corr_info

    # Ensure pubsubcache has updated correction params
    assert my_scheduler.pubsubcache._corr_info == corr_info
    assert my_scheduler.pubsubcache._update_weight == update_weight

    neg_corr_info = copy.deepcopy(corr_info)
    neg_corr_info.vec = -neg_corr_info.vec
    neg_corr_info.rate = -neg_corr_info.rate
    # Ensure router has updated correction params
    assert my_scheduler.router._corr_info == neg_corr_info
    assert my_scheduler.router._update_weight == update_weight

    # Kill context (needed due to funky lack of pytest fixture)
    ctx.destroy()


def test_update_curr_corr_info(pub_url, psc_url, server_url, router_url, ctx,
                               csv_attribs, channel_id, corr_info,
                               monkeypatch, update_weight, dt1, dt2, vec,
                               unit):
    logger.info("Validate corr_info is updated as expected for all cases.")
    scan = scan_pb2.Scan2d()
    # We need length_units to have some unit, so we can do conversions
    # when updating the Scan2d/ScanParameters2d protos.
    scan.params.spatial.length_units = 'nm'
    scan.timestamp.FromDatetime(dt2)  # Update timestamp

    logger.info("If no total_corr_info and no snapshot, total_corr_info "
                "stays None.")
    my_scheduler = create_scheduler_and_ios(pub_url, psc_url, server_url,
                                            router_url, None, ctx, csv_attribs,
                                            channel_id, update_weight)
    my_scheduler.total_corr_info = None
    my_scheduler._update_curr_corr_info(scan, None)
    assert my_scheduler.total_corr_info is None

    logger.info("If total_corr_info and no snapshot, total_corr_info "
                "updates considering it's drift rate")
    my_scheduler.total_corr_info = corr_info
    my_scheduler._update_curr_corr_info(scan, None)
    # Delta corr info is vec * (dt2 - dt1) = vec
    # Therefore, total is vec + vec.
    expected_corr_info = correction.CorrectionInfo(dt2, vec + vec, vec,
                                                   unit)
    assert my_scheduler.total_corr_info == expected_corr_info

    logger.info("If no total_corr_info and a snapshot, total_corr_info "
                "updates to it")
    my_scheduler.total_corr_info = None
    snapshot = correction.DriftSnapshot(dt1, dt2, -vec, unit)
    my_scheduler._update_curr_corr_info(scan, snapshot)
    # Delta corr info is the negative of the snapshot, so vec. (Remember, our
    # correction is the negation of the drift we detect via the snapshot).
    # Therefore, total is vec.
    expected_corr_info = correction.CorrectionInfo(dt2, vec, vec,
                                                   unit)
    assert my_scheduler.total_corr_info == expected_corr_info

    logger.info("If total_corr_info and a snapshot, total_corr_info "
                "updates considering it's drift rate and the snapshot")
    my_scheduler.update_weight = 1.0
    my_scheduler.total_corr_info = corr_info
    snapshot = correction.DriftSnapshot(dt1, dt2, vec, unit)
    my_scheduler._update_curr_corr_info(scan, snapshot)
    # Drift corr info is the negative of the snapshot, so -vec.
    # Delta corr info is vec from old rate + new vec, so vec - vec = 0.
    # The total is old corr vec + new vec = old corr vec.
    expected_corr_info = correction.CorrectionInfo(dt2, vec, [0, 0],
                                                   unit)

    logger.info("Same as last, but with update_weight=0.9")
    my_scheduler.update_weight = 0.9
    my_scheduler.total_corr_info = corr_info
    logger.warning(f'corr_info: {corr_info}')  # TODO Remove me
    snapshot = correction.DriftSnapshot(dt1, dt2, vec, unit)
    my_scheduler._update_curr_corr_info(scan, snapshot)
    # Drift corr info is the negative of the snapshot, so -vec.
    # Delta corr info is vec from old rate + new vec, so vec - vec = 0.
    # The total is:
    # a)  update_vec = (1-w) * (old rate * del_t) + w * new vec = 0.1 * vec.
    # b)  old_vec + update_vec = 1.1 * vec
    expected_corr_info = correction.CorrectionInfo(dt2, 1.1 * vec, 0.1 * vec,
                                                   unit)
    assert my_scheduler.total_corr_info == expected_corr_info

    # Kill context (needed due to funky lack of pytest fixture)
    ctx.destroy()


@pytest.fixture
def roi1():
    tl = geometry_pb2.Point2d(x=0, y=0)
    size = geometry_pb2.Size2d(x=5, y=5)
    return geometry_pb2.RotRect2d(top_left=tl, size=size)


@pytest.fixture
def roi2():  # requires rescan
    tl = geometry_pb2.Point2d(x=-2.5, y=-2.5)
    size = geometry_pb2.Size2d(x=5, y=5)
    return geometry_pb2.RotRect2d(top_left=tl, size=size)


@pytest.fixture
def roi3():  # does not require rescan
    tl = geometry_pb2.Point2d(x=.5, y=.5)
    size = geometry_pb2.Size2d(x=5, y=5)
    return geometry_pb2.RotRect2d(top_left=tl, size=size)


def test_determine_redo_scan(pub_url, psc_url, server_url, router_url, ctx,
                             rescan_url, csv_attribs, channel_id, corr_info,
                             monkeypatch, update_weight, dt1, dt2, vec,
                             unit, roi1, roi2, roi3):
    logger.info("Ensure we send out redo scans as needed, and the logic is as "
                "expected.")
    my_scheduler = create_scheduler_and_ios(pub_url, psc_url, server_url,
                                            router_url, rescan_url, ctx,
                                            csv_attribs,
                                            channel_id, update_weight)
    my_scheduler.total_corr_info = correction.CorrectionInfo(
        dt1, np.array([0, 0]), np.array([0, 0]))

    # Set up original 'scan params' request
    requested_scan_params = scan_pb2.ScanParameters2d()
    requested_scan_params.spatial.roi.CopyFrom(roi2)
    my_scheduler.router._last_scan_params = requested_scan_params

    sub = subscriber.Subscriber(rescan_url, ctx=ctx)

    logger.debug("First, perfect intersection scans should not require a rescan.")
    # Note: corr_info is [0, 0], so there is no difference b/w uncorrected_scan
    # and true_scan in code.
    uncorrected_scan = scan_pb2.Scan2d()
    uncorrected_scan.params.spatial.roi.CopyFrom(roi1)
    prior_scan = scan_pb2.Scan2d()
    prior_scan.params.spatial.roi.CopyFrom(roi1)

    my_scheduler._determine_redo_scan(uncorrected_scan, prior_scan)
    assert sub.poll_and_store() is None

    logger.debug("If we require a rescan, our subscriber receives it.")
    prior_scan = scan_pb2.Scan2d()
    prior_scan.params.spatial.roi.CopyFrom(roi2)
    my_scheduler._determine_redo_scan(uncorrected_scan, prior_scan)
    received = sub.poll_and_store()
    __, proto = received[0]  # first received of list of messages
    assert proto == requested_scan_params

    logger.debug("If the intersection is big enough, no rescan required.")
    prior_scan = scan_pb2.Scan2d()
    prior_scan.params.spatial.roi.CopyFrom(roi3)
    my_scheduler._determine_redo_scan(uncorrected_scan, prior_scan)
    assert sub.poll_and_store() is None

    # logger.debug("Go back to a rescan case, but let's ensure we send the data "
    #              "in the sample coordinate system!")
    # my_scheduler.total_corr_info = correction.CorrectionInfo(
    #     dt1, np.array([1, 1]), np.array([0, 0]))
    # prior_scan = scan_pb2.Scan2d()
    # prior_scan.params.spatial.roi.CopyFrom(roi2)
    # my_scheduler._determine_redo_scan(uncorrected_scan, prior_scan)

    # expected_params = uncorrected_scan.params
    # scs_tl = geometry_pb2.Point2d(x=1.0, y=1.0)
    # expected_params.spatial.roi.top_left.CopyFrom(scs_tl)
    # received = sub.poll_and_store()
    # __, proto = received[0]  # first received of list of messages
    # assert proto == expected_params

    # Kill context (needed due to funky lack of pytest fixture)
    ctx.destroy()


@pytest.fixture
def filename():
    return 'filename'


def test_metadata_writing(dt1, vec, unit, roi1, filename):
    logger.info('Ensuring metadata row writing works.')

    corr_info = correction.CorrectionInfo(dt1, vec, -vec, unit)

    scan = scan_pb2.Scan2d()
    scan.params.spatial.roi.CopyFrom(roi1)
    scan.filename = filename
    scan.timestamp.FromDatetime(dt1)

    row_vals = scheduler.get_metadata_row_v2(scan, corr_info, True)
    expected_row_vals = [dt1.isoformat(), filename, corr_info.vec[0],
                         corr_info.vec[1], corr_info.unit, corr_info.rate[0],
                         corr_info.rate[1], corr_info.unit + '/s',
                         True]
    assert row_vals == expected_row_vals

    row_vals = scheduler.get_metadata_row_v2(scan, None, True)
    expected_row_vals = [dt1.isoformat(), filename, None,
                         None, None, None, None, None, True]
    assert row_vals == expected_row_vals
