"""Holds logic for a drift-corrected scheduler.

The scheduler in here will use logic from drift.py in order to automatically
convert from the piezo coordinate system (PCS) to the 'true' sample coordinate
system (SCS). The idea is to track drift and from it determine the correction
vector needed to perform on any incoming request or outgoing message such that
it is always in the estimated SCS.
"""

from collections import deque
import logging
import numpy as np
import copy
import datetime as dt
import matplotlib.pyplot as plt
from google.protobuf.message import Message
from google.protobuf.message_factory import GetMessageClass

from . import drift, correction
from ..microscope import scheduler
from ...utils import csv
from ...utils import proto_geo
from ...utils.units import convert_list
from ...io.control import router
from ...io.pubsub import cache
from ...io.pubsub.logic import cache_logic

from ...io.protos.generated import control_pb2
from ...io.protos.generated import scan_pb2
from ...io.protos.generated import spec_pb2
from ...io.protos.generated import geometry_pb2


logger = logging.getLogger(__name__)


ANGLE_FIELD = 'angle'  # Used to check if an angle is set in scan_params
PLT_LAYOUT = 'constrained'


# kwarg keys for Scheduler constructor
CACHE_KEY = 'pubsubcache'
ROUTER_KEY = 'router'


def get_converted_and_updated_vec(corr_info: correction.CorrectionInfo,
                                  unit: str,
                                  curr_dt: dt.datetime | None
                                  ) -> np.ndarray:
    """Get correction vector, converted to proto units and drift corrected.

    Given the current CorrectionInfo, compute the associated correction vector
    in the provided spatial unit. If a DateTime is provided, additionally
    update the vector considering the drift rate.

    Args:
        corr_info: CorrectionInfo associated with current drifting.
        unit: spatial unit we want the correction vector in.
        curr_dt: the current DateTime, used to update the vector for drift. If
            None, we do not update for drift.

    Returns:
        the correction vector after (optionally) correcting for drift and
            converting to desired spatial units.

    """
    drift_vec = correction.estimate_correction_no_snapshot(corr_info, curr_dt)
    corr_info = correction.update_total_correction(corr_info, drift_vec)
    # TODO: Should we still be using this?

    desired_units = (unit, unit)
    corr_units = (corr_info.unit, corr_info.unit)
    corr_vec = np.array(convert_list(corr_info.vec.tolist(),
                                     corr_units, desired_units))
    return corr_vec


def correct_spatial_aspects(proto: scan_pb2.SpatialAspects,
                            corr_info: correction.CorrectionInfo,
                            curr_dt: dt.datetime | None
                            ) -> scan_pb2.SpatialAspects:
    """Correct SpatialAspects given CorrectionInfo and (optional) DateTime.

    Correct SpatialAspects for drift by adding a correction vector to its
    ROI position.

    Args:
        proto: SpatialAspects to update.
        corr_info: CorrectionInfo associated with current drifting.
        curr_dt: the current DateTime, used to update the vector for drift. If
            None, we do not update for drift.

    Returns:
        updated proto of SpatialAspects.
    """
    logger.trace(f'Spatial Aspects before correcting: {proto}')
    orig_tl = np.array([proto.roi.top_left.x, proto.roi.top_left.y])
    corr_vec = get_converted_and_updated_vec(corr_info,
                                             proto.length_units,
                                             curr_dt)

    corrected_tl = orig_tl + corr_vec
    corrected_tl_pt2d = geometry_pb2.Point2d(x=corrected_tl[0],
                                             y=corrected_tl[1])
    proto.roi.top_left.CopyFrom(corrected_tl_pt2d)
    logger.trace(f'Spatial Aspects after correcting: {proto}')
    return proto


def correct_probe_position(proto: spec_pb2.ProbePosition,
                           corr_info: correction.CorrectionInfo,
                           curr_dt: dt.datetime | None
                           ) -> spec_pb2.ProbePosition:
    """Correct ProbePosition given CorrectionInfo and (optional) DateTime.

    Correct ProbePosition for drift by adding a correction vector to its
    ROI position.

    Args:
        proto: ProbePosition to update.
        corr_info: CorrectionInfo associated with current drifting.
        curr_dt: the current DateTime, used to update the vector for drift. If
            None, we do not update for drift.

    Returns:
        updated proto of ProbePosition.
    """
    logger.trace(f'Probe Pos before correcting: {proto}')
    orig_pos = np.array([proto.point.y, proto.point.x])
    corr_vec = get_converted_and_updated_vec(corr_info,
                                             proto.units, curr_dt)

    corrected_pos = orig_pos + corr_vec
    corrected_pos_pt2d = geometry_pb2.Point2d(x=corrected_pos[0],
                                              y=corrected_pos[1])
    proto.point.CopyFrom(corrected_pos_pt2d)
    logger.trace(f'Probe Pos after correcting: {proto}')
    return proto


# TODO: need to feed DateTime of latest event. if None, we do not
# use drift rate to estimate
def cs_correct_proto(proto: Message, corr_info: correction.CorrectionInfo,
                     curr_dt: dt.datetime | None) -> Message:
    """Recursively go through a protobuf, correcting CS-based fields.

    When we find fields that should be corrected for an updated coordinate
    system, we fix them.

    Args:
        proto: Message to update.
        corr_info: CorrectionInfo associated with current drifting.
        curr_dt: the current DateTime, used to update the vector for drift. If
            None, we do not update for drift.

    Returns:
        updated Message.
    """
    constructor = GetMessageClass(proto.DESCRIPTOR)
    vals_dict = {}
    # NOTE: any new protos that have spatial fields should be added here!
    for field in proto.DESCRIPTOR.fields:
        val = getattr(proto, field.name)
        new_val = None
        if isinstance(val, scan_pb2.SpatialAspects):
            new_val = correct_spatial_aspects(val, corr_info, curr_dt)
        elif isinstance(val, spec_pb2.ProbePosition):
            new_val = correct_probe_position(val, corr_info, curr_dt)
        elif isinstance(val, Message):
            new_val = cs_correct_proto(val, corr_info, curr_dt)

        if new_val:
            vals_dict[field.name] = new_val

    partial_proto = constructor(**vals_dict)
    proto.MergeFrom(partial_proto)
    return proto


class CSCorrectedRouter(router.ControlRouter):
    """Corrects CS data before it is sent out via the router."""

    def __init__(self):
        """Init - this class requires usage of from_parent."""
        self._corr_info = None

    def _handle_send_req(self, req: control_pb2.ControlRequest,
                         proto: Message) -> (control_pb2.ControlResponse,
                                             Message | int | None):
        """Override to correct CS data before sending out."""
        if self._corr_info is not None:
            # TODO: Should we have an option to determine whether or not we
            # correct for drift rate? What if our estimate is poop?
            curr_dt = dt.datetime.now(dt.timezone.utc)
            proto = cs_correct_proto(proto, self._corr_info, curr_dt)

        # Send out
        return super()._handle_send_req(req, proto)

    @classmethod
    def from_parent(cls, parent):
        """Actual construction method, given a parent instance."""
        child = CSCorrectedRouter()
        child._ctx = parent._ctx
        child._backend_url = parent._backend_url
        child._backend = parent._backend
        child._frontend = parent._frontend
        child._problems_set = parent._problems_set
        child._control_mode = parent._control_mode
        child._client_in_control_id = parent._client_in_control_id
        child._poll_timeout_ms = parent._poll_timeout_ms
        child._request_timeout_ms = parent._request_timeout_ms
        child.shutdown_was_requested = parent.shutdown_was_requested
        return child

    def update_correction_info(self, corr_info: correction.CorrectionInfo):
        """Update our correction vector.

        The correction vector is PCS -> SCS. The router is receiving
        requests from components in the SCS, to be sent to the Microscope.
        Thus, we want to go SCS -> PCS, which means we must invert our
        translation vector.
        """
        self._corr_info = copy.deepcopy(corr_info)
        self._corr_info.vec = -self._corr_info.vec  # We want SCS -> PCS


class CSCorrectedCache(cache.PubSubCache):
    """Corrects CS data before it is sent out to subscribers."""

    SCAN_ID = cache_logic.CacheLogic.get_envelope_for_proto(scan_pb2.Scan2d())

    def __init__(self, **kwargs):
        """Init - this class requires usage of from_parent."""
        self._corr_info = None
        self._observers = []

    def bind_to(self, callback):
        """Bind callback to scan having been received."""
        self._observers.append(callback)

    def send_message(self, proto: Message):
        """Override to correct CS data before sending out."""
        if isinstance(proto, scan_pb2.Scan2d):
            # Call observers so they can update their logic on a new scan
            # change.
            for callback in self._observers:
                callback(proto)

        super().send_message(proto)

    @classmethod
    def from_parent(cls, parent):
        """Actual construction method, given a parent instance."""
        child = CSCorrectedCache()
        child.cache = parent.cache
        child._sub_extract_proto = parent._sub_extract_proto
        child._extract_proto_kwargs = parent._extract_proto_kwargs
        child._pub_get_envelope_for_proto = parent._pub_get_envelope_for_proto
        child._get_envelope_kwargs = parent._get_envelope_kwargs
        child._update_cache = parent._update_cache
        child._update_cache_kwargs = parent._update_cache_kwargs
        child._poll_timeout_ms = parent._poll_timeout_ms
        child._frontend = parent._frontend
        child._backend = parent._backend
        child._poller = parent._poller
        return child

    def update_correction_info(self, corr_info: correction.CorrectionInfo):
        """Update our correction vector.

        The correction vector is PCS -> SCS. The PubSubCache receives requests
        from the Microscope in PCS, and must convert it to SCS (what components
        expect). Thus, we use PCS -> SCS< which is the correction vector
        without modifications.
        """
        self._corr_info = copy.deepcopy(corr_info)


class CSCorrectedScheduler(scheduler.MicroscopeScheduler):
    """Corrects coordinate system data in addition to being a scheduler.

    TODO: UPDATE ME!

    The CSCorrectedScheduler is a wrapper on top of a standard
    MicroscopeScheduler, where it additionally attempts to estimate drift
    in the system and correct for it. Thus:
    - any coordinate data that is sent to the Microscope will be 'corrected'
    such that it is in the piezo coordinate system (PCS).
    - any coordinate data published by the Microscope will be 'corrected' such
    that it is in the sample coordinate system (SCS).

    We (as developers/researchers) would like to think in a drift-free SCS.
    In reality the sample and tip are drifting over time due to thermal
    effects of all atoms in the system. To correct for it, we must determine
    the mapping from our piezo coordinate system (the CS tied to the voltage
    we apply to the piezoelectric material and the associated change in its
    position) to the 'true' sample coordinate system (where the tip is relative
    to a sample origin).

    We use a DriftModel to estimate a correction vector between any two
    scans that *should* correspond to the same position in the SCS. The vector
    tells us the translation drift that occurred between the two scans over
    that time period. By holding a correction vector that is updated from
    the start of the experiment, we can maintain an appropriate mapping from
    SCS to PCS (and vice-versa).

    For post-experiment usage, we save a csv file containing the relative and
    absolute correction vectors computed over time at filepath.

    In order to determine correction vectors, the DriftModel needs to compare
    each current scan with a prior scan that intersects over the same PCS. To
    do so, we consider the scans available in the current cache *before*
    updating. Thus, it is important that the cache is set up such that we
    are likely to have a prior scan to compare to.

    For each given scan, there are two possible paths for estimating the
    correction vector:
    1. We find an 'matching' scan in the cache, *and* the drift estimation
    algorithm is able to reliably determine a correction vector (i.e. its
    fitting score is high enough). Here, we compute a new CorrectionInfo and
    add it to our history (see compute_drift_snapshot and
    _update_drift_snapshots).
    2. We do not find a 'matching' scan in the cache (or the fit was not
    good enough). In this case, we estimate a correction vector based on our
    history of CorrectionInfos (see estimate_correction_vec).

    We should also clarify what 'matching' means when we find a match between
    the latest scan and those in the cache. A scan pair is considered 'matched'
    if:
    - The intersection area of the scan's physical scan regions is sufficiently
    large (we use min_intersection_ratio for this).
    - The two scans are close enough in their spatial resolutions (we use
    min_spatial_res_ratio for this). If the spatial resolutions are too
    distinct, it is unlikely we will find matching keypoints between them
    (due to too-different signals).

    Attributes:
        drift_model: the DriftModel used to estimate a correction vector
            between two scans.
        drift_snapshots: a deque of DriftSnapshots that has been collected
            over time.
        total_corr_info: total CorrectionInfo, fed to IO nodes so they can
            correct for the PCS-SCS transform.
        filepath: path where we save our csv file containing all correction
            data associated to scans.
        min_intersection_ratio: minimum intersection area to scan ratio to
            accept two scans as intersecting. The scan area in our
            numerator is that of the newer scan.
        min_spatial_res_ratio: minimum spatial resolution ratio between two
            scans to accept them as matching. If the two scans have vastly
            different spatial resolutions, it is unlikely we will find
            keypoint matches!
        max_fitting_score: maximum fitting score for a 'matched' scan to be
            considered fit properly. The default assumes a RANSAC FittingMethod
            (so we yell at you if it's not the case). Note that a score of 0
            is ideal here, so the lower the better.
        display_fit: visualize the fitting while it runs.
        figure: figure used to visualize (if applicable).
    """

    SCAN_ID = cache_logic.CacheLogic.get_envelope_for_proto(scan_pb2.Scan2d())
    DEFAULT_CSV_ATTRIBUTES = csv.CSVAttributes('./drift_correction.csv')
    DEFAULT_MIN_INTERSECTION_RATIO = 0.25
    DEFAULT_MIN_SPATIAL_RES_RATIO = 0.25
    # This default fitting score is linked to RANSAC minimum residual threshold
    DEFAULT_MAX_FITTING_SCORE = drift.DEFAULT_RESIDUAL_THRESH_PERCENT
    DEFAULT_DISPLAY_FIT = True

    CSV_FIELDS = ['timestamp', 'filename', 'pcs_to_scs_trans',
                  'estimated_from_drift?']

    def __init__(self, drift_model: drift.DriftModel | None = None,
                 csv_attribs: csv.CSVAttributes = DEFAULT_CSV_ATTRIBUTES,
                 min_intersection_ratio: float = DEFAULT_MIN_INTERSECTION_RATIO,
                 min_spatial_res_ratio: float = DEFAULT_MIN_SPATIAL_RES_RATIO,
                 max_fitting_score: float = DEFAULT_MAX_FITTING_SCORE,
                 display_fit: bool = DEFAULT_DISPLAY_FIT, **kwargs):
        """Initialize our correction scheduler."""
        self.drift_model = (drift.create_drift_model() if drift_model is None
                            else drift_model)
        self.csv_attribs = csv_attribs
        self.min_intersection_ratio = min_intersection_ratio
        self.min_spatial_res_ratio = min_spatial_res_ratio
        self.max_fitting_score = max_fitting_score

        # TODO: Should this be a default in constructor of dataclass!?
        self.total_corr_info = correction.CorrectionInfo()

        self.display_fit = display_fit
        self.figure = plt.figure(layout=PLT_LAYOUT)
        plt.show(block=False)  # TODO should this be elsewhere? At start?

        # Warn user if using default fitting score and not RANSAC fitting
        if (self.max_fitting_score == self.DEFAULT_MAX_FITTING_SCORE and
                self.drift_model.fitting != drift.FittingMethod.RANSAC):
            logger.warning('Using default fitting score for fitting method '
                           f'{drift_model.fitting} (i.e. not RANSAC). This '
                           'is probably too low!')

        # Create our wrapper router and cache
        kwargs[ROUTER_KEY] = CSCorrectedRouter.from_parent(
            kwargs[ROUTER_KEY])
        kwargs[CACHE_KEY] = CSCorrectedCache.from_parent(
            kwargs[CACHE_KEY])

        super().__init__(**kwargs)

        # In order to update our logic, we bind update to the
        # cache receiving a scan (i.e. updates from the Microscope).
        self.pubsubcache.bind_to(self.update)

        csv.init_csv_file(self.csv_attribs, self.CSV_FIELDS)

    @staticmethod
    def _get_metadata_row(scan: scan_pb2.Scan2d,
                          corr_info: correction.CorrectionInfo,
                          estimated_from_vec: bool) -> [str]:
        # TODO: update me! add drift rate???
        row_vals = [scan.timestamp.seconds,
                    scan.filename,
                    corr_info.vec,
                    estimated_from_vec]
        return row_vals

    def _get_scans_from_cache(self):
        # The cache is a key:val map of deques of items.
        # So we need to filter through each deque and concat the values
        # if they are scans.
        scan_deques = [val for key, val in self.pubsubcache.cache.items()
                       if self.SCAN_ID in key]

        # Go from list of lists to flattened single list (extending a new list)
        concatenated_scans = []
        for scan_deque in scan_deques:
            concatenated_scans.extend(scan_deque)
        return concatenated_scans

    def _get_drift_snapshot(self, new_scan: scan_pb2.Scan2d
                            ) -> correction.DriftSnapshot | None:
        """Estimate drift for the new scan."""
        self.figure.clear()  # Clear figure before showing

        matched_scan = proto_geo.get_latest_intersection(
            self._get_scans_from_cache(), new_scan,
            self.min_intersection_ratio, self.min_spatial_res_ratio)

        scan_was_matched = matched_scan is not None
        snapshot = None
        if scan_was_matched:
            snapshot = correction.compute_drift_snapshot(
                matched_scan, new_scan, self.drift_model,
                self.max_fitting_score, self.display_fit,
                self.figure)

        plt.show(block=False)  # TODO should this be elsewhere? At start?
        return snapshot

    def _update_curr_corr_info(self, new_scan: scan_pb2.Scan2d,
                               snapshot: correction.DriftSnapshot | None):
        """Update the current CorrectionInfo based on the new incoming scan.

        Given a new scan and knowledge of whether we matched a scan to it from
        our history, we update the current CorrectionInfo. If there was a
        match, we can simply grab the latest DriftSnapshot. If not, we
        estimate a correction vector based on the history.
        """
        if snapshot is not None:
            logger.warning('Scan matched.')
            corr_info = correction.estimate_correction_from_snapshot(
                snapshot, self.total_corr_info)
            logger.warning(f'delta corr_info: {corr_info}')
        else:
            logger.warning('No match. Estimating from prior info.')
            corr_info = correction.estimate_correction_no_snapshot(
                self.total_corr_info, new_scan.timestamp.ToDatetime(
                    dt.timezone.utc))
            logger.warning(f'delta corr_info: {corr_info}')

        new_tot_corr_info = correction.update_total_correction(
            self.total_corr_info, corr_info)
        logger.warning(f'total corr_info: {new_tot_corr_info}')

        drift_has_changed = not np.all(np.isclose(
            new_tot_corr_info.vec, self.total_corr_info.vec))
        self.total_corr_info = new_tot_corr_info

        # Notify logger if correction vec has changed
        if drift_has_changed:
            logger.info('The PCS-to-SCS correction vector has changed'
                        f': {self.total_corr_info.vec} '
                        f'{self.total_corr_info.unit}.')
            # TODO: print drift rate too??? put in separate method?

    def _update_io(self):
        """Update IO nodes with latest CorrectionInfo.

        Inform our IO nodes of the latest CorrectionInfo, so they may use
        it to 'correct' the coordinate system accordingly.
        """
        self.pubsubcache.update_correction_info(self.total_corr_info)
        self.router.update_correction_info(self.total_corr_info)

    def update(self, new_scan: scan_pb2.Scan2d):
        """Update correction infos given a new scan.

        This method updates internal logic for our CS Correction given that
        a new scan has been received. We do the following:
        - Update CorrectionInfos: try to match the new scan to a scan in our
        cache. If we find a match, we have a new CorrectionInfo (containing the
        translation vector beween the scans) to add to our history.
        - Update the correction vec: if we found a match between the new scan
        and history, we can use the latest CorrectionInfo to get our correction
        vector estimate. If we did not, we can estimate this vector from our
        history.
        - Save this update in our historical csv file.

        Args:
            new_scan: the incoming scan, which we use to update our correction
                estimates.
        """
#        logger.warning('New scan. Checking if we find a match...')
#        logger.warning(f'New scan position: {new_scan.params.spatial}')

        # First things first: the received scan is in the PCS. Convert to our
        # *current* SCS.
        new_scan = cs_correct_proto(new_scan, self.total_corr_info,
                                    new_scan.timestamp.ToDatetime(
                                        dt.timezone.utc))

        snapshot = self._get_drift_snapshot(new_scan)
#        logger.warning(f'Match: {scan_was_matched}')
        self._update_curr_corr_info(new_scan, snapshot)

        # TODO: What if the drift is too much? At what point do we take over and
        # redo the scan?

        estimated_from_vec = snapshot is None
        row_vals = self._get_metadata_row(new_scan, self.total_corr_info,
                                          estimated_from_vec)
        csv.save_csv_row(self.csv_attribs, self.CSV_FIELDS,
                         row_vals)
        self._update_io()

    def run_per_loop(self):
        """Override to update figures every loop."""
        super().run_per_loop()
        self.figure.canvas.draw_idle()
        self.figure.canvas.flush_events()
