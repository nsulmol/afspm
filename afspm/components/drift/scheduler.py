"""Holds logic for a drift-corrected scheduler.

The scheduler in here will use logic from drift.py in order to automatically
convert from the tip coordinate system (TCS) to the 'true' sample coordinate
system (SCS). The idea is to track drift and from it determine the correction
vector needed to perform on any incoming request or outgoing message such that
it is always in the estimated SCS.
"""

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


# Update weight for averaging new data
DEFAULT_UPDATE_WEIGHT = 1.00  # No averaging


def get_converted_and_updated_vec(corr_info: correction.CorrectionInfo,
                                  update_weight: float,
                                  unit: str,
                                  curr_dt: dt.datetime | None
                                  ) -> np.ndarray:
    """Get correction vector, converted to proto units and drift corrected.

    Given the current CorrectionInfo, compute the associated correction vector
    in the provided spatial unit. If a DateTime is provided, additionally
    update the vector considering the drift rate.

    Args:
        corr_info: CorrectionInfo associated with current drifting.
        update_weight: weight applied for updating corr_info.
        unit: spatial unit we want the correction vector in.
        curr_dt: the current DateTime, used to update the vector for drift.
            If None, do not correct for temporal drift.

    Returns:
        the correction vector after (optionally) correcting for drift and
            converting to desired spatial units.

    """
    if curr_dt is not None:
        drift_vec = correction.estimate_correction_no_snapshot(corr_info,
                                                               curr_dt)
        corr_info = correction.update_total_correction(corr_info, drift_vec,
                                                       update_weight)

    desired_units = (unit, unit)
    corr_units = (corr_info.unit, corr_info.unit)

    corr_vec = np.array(convert_list(corr_info.vec.tolist(),
                                     corr_units, desired_units))
    return corr_vec


def correct_spatial_aspects(proto: scan_pb2.SpatialAspects,
                            corr_info: correction.CorrectionInfo,
                            update_weight: float,
                            curr_dt: dt.datetime | None
                            ) -> scan_pb2.SpatialAspects:
    """Correct SpatialAspects given CorrectionInfo and (optional) DateTime.

    Correct SpatialAspects for drift by adding a correction vector to its
    ROI position.

    Args:
        proto: SpatialAspects to update.
        corr_info: CorrectionInfo associated with current drifting.
        update_weight: weight applied for updating corr_info.
        curr_dt: the current DateTime, used to update the vector for drift.
            If None, do not correct for temporal drift.

    Returns:
        updated proto of SpatialAspects.
    """
    logger.trace(f'Spatial Aspects before correcting: {proto}')
    orig_tl = np.array([proto.roi.top_left.x, proto.roi.top_left.y])
    corr_vec = get_converted_and_updated_vec(corr_info, update_weight,
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
                           update_weight: float,
                           curr_dt: dt.datetime | None
                           ) -> spec_pb2.ProbePosition:
    """Correct ProbePosition given CorrectionInfo and (optional) DateTime.

    Correct ProbePosition for drift by adding a correction vector to its
    ROI position.

    Args:
        proto: ProbePosition to update.
        corr_info: CorrectionInfo associated with current drifting.
        update_weight: weight applied for updating corr_info.
        curr_dt: the current DateTime, used to update the vector for drift.
            If None, do not correct for temporal drift.

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


def cs_correct_proto(proto_in: Message,
                     corr_info: correction.CorrectionInfo | None,
                     update_weight: float, curr_dt: dt.datetime | None
                     ) -> Message:
    """Recursively go through a protobuf, correcting CS-based fields.

    When we find fields that should be corrected for an updated coordinate
    system, we fix them.

    Args:
        proto_in: Message to update.
        corr_info: CorrectionInfo associated with current drifting. If None,
            we do not correct.
        update_weight: weight applied for updating corr_info.
        curr_dt: the current DateTime, used to update the vector for drift.
            If None, do not correct for temporal drift.

    Returns:
        updated Message.
    """
    if corr_info is None:  # We cannot do any correction w/o corr_info!
        return proto_in

    proto = copy.deepcopy(proto_in)
    constructor = GetMessageClass(proto.DESCRIPTOR)
    vals_dict = {}
    # NOTE: any new protos that have spatial fields should be added here!
    for field in proto.DESCRIPTOR.fields:
        val = getattr(proto, field.name)
        new_val = None
        if isinstance(val, scan_pb2.SpatialAspects):
            new_val = correct_spatial_aspects(val, corr_info, update_weight,
                                              curr_dt)
        elif isinstance(val, spec_pb2.ProbePosition):
            new_val = correct_probe_position(val, corr_info, update_weight,
                                             curr_dt)
        elif isinstance(val, Message):
            new_val = cs_correct_proto(val, corr_info, update_weight,
                                       curr_dt)

        if new_val:
            vals_dict[field.name] = new_val

    partial_proto = constructor(**vals_dict)
    proto.MergeFrom(partial_proto)
    return proto


class CSCorrectedRouter(router.ControlRouter):
    """Corrects CS data before it is sent out via the router."""

    def __init__(self, **kwargs):
        """Init - this class requires usage of from_parent."""
        self._corr_info = None
        self._update_weight = DEFAULT_UPDATE_WEIGHT
        self._last_scan_params = None

    def _handle_send_req(self, req: control_pb2.ControlRequest,
                         proto: Message) -> (control_pb2.ControlResponse,
                                             Message | int | None):
        """Override to correct CS data before sending out.

        We also store the latest scan params on success, so we can
        use these to send rescans. This is important, because these
        params are in the Sample CS and are the actual requested region.
        When grabbing from the cache, our regions are the resultant regions,
        which may include drift!
        """
        scan_params = None
        if isinstance(proto, scan_pb2.ScanParameters2d):
            scan_params = proto

        # TODO: Should we have an option to determine whether or not we
        # correct for drift rate? What if our estimate is poop?
        curr_dt = dt.datetime.now(dt.timezone.utc)
        proto = cs_correct_proto(proto, self._corr_info,
                                 self._update_weight, curr_dt)

        # Get response
        response, message = super()._handle_send_req(req, proto)

        # Store SCS scan params if we succeeded at setting them.
        if scan_params and response == control_pb2.ControlResponse.REP_SUCCESS:
            self._last_scan_params = scan_params

        # Send out
        return response, message

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

    def update_correction_info(self,
                               corr_info: correction.CorrectionInfo | None,
                               update_weight: float):
        """Update our correction vector.

        The correction vector is TCS -> SCS. The router is receiving
        requests from components in the SCS, to be sent to the Microscope.
        Thus, we want to go SCS -> TCS, which means we must invert our
        translation vector.
        """
        self._corr_info = copy.deepcopy(corr_info)
        if self._corr_info is not None:
            # We want SCS -> TCS
            self._corr_info.vec = -self._corr_info.vec
            self._corr_info.rate = -self._corr_info.rate

        self._update_weight = update_weight


class CSCorrectedCache(cache.PubSubCache):
    """Corrects CS data before it is sent out to subscribers."""

    SCAN_ID = cache_logic.CacheLogic.get_envelope_for_proto(scan_pb2.Scan2d())

    def __init__(self, **kwargs):
        """Init - this class requires usage of from_parent."""
        self._observers = []
        self._corr_info = None
        self._update_weight = DEFAULT_UPDATE_WEIGHT

    def bind_to(self, callback):
        """Bind callback to scan having been received."""
        self._observers.append(callback)

    def send_message(self, proto: Message):
        """Override to correct CS data before sending out."""
        for callback in self._observers:
            callback(proto)

        # Correct CS data of proto
        # Get curr_dt for Scans and Specs, so we ensure their locations
        # account for drift rate.
        curr_dt = dt.datetime.now(dt.timezone.utc)
        if isinstance(proto, scan_pb2.Scan2d):
            curr_dt = proto.timestamp.ToDatetime(dt.timezone.utc)
        elif isinstance(proto, spec_pb2.Spec1d):
            curr_dt = proto.timestamp.ToDatetime(dt.timezone.utc)
        proto = cs_correct_proto(proto, self._corr_info,
                                 self._update_weight, curr_dt)

        super().send_message(proto)  # Add to cache after CS switch.

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

    def update_correction_info(self,
                               corr_info: correction.CorrectionInfo | None,
                               update_weight: float):
        """Update our correction vector."""
        self._corr_info = copy.deepcopy(corr_info)
        self._update_weight = update_weight


class CSCorrectedScheduler(scheduler.MicroscopeScheduler):
    """Corrects coordinate system data in addition to being a scheduler.

    The CSCorrectedScheduler is a wrapper on top of a standard
    MicroscopeScheduler, where it additionally attempts to estimate drift
    in the system and correct for it. Thus:
    - any coordinate data that is sent to the Microscope will be 'corrected'
    such that it is in the tip coordinate system (TCS).
    - any coordinate data published by the Microscope will be 'corrected' such
    that it is in the sample coordinate system (SCS).

    We (as developers/researchers) would like to think in a drift-free SCS.
    In reality the sample and tip are drifting over time due to thermal
    effects of all atoms in the system. To correct for it, we must determine
    the mapping from our tip coordinate system (the CS tied to the voltage
    we apply to the piezoelectric material and the associated change in its
    position) to the 'true' sample coordinate system (where the tip is relative
    to a sample origin).

    We use a DriftModel to estimate a correction vector between any two
    scans that *should* correspond to the same position in the SCS. The vector
    tells us the translation drift that occurred between the two scans over
    that time period. By holding a correction vector that is updated from
    the start of the experiment, we can maintain an appropriate mapping from
    SCS to TCS (and vice-versa).

    For post-experiment usage, we save a csv file containing the correction
    vector and drift rate computed over time at filepath.

    In order to determine correction vectors, the DriftModel needs to compare
    each current scan with a prior scan that intersects over the same TCS. To
    do so, we consider the scans available in the current cache *before*
    updating. Thus, it is important that the cache is set up such that we
    are likely to have a prior scan to compare to.

    For each given scan, there are two possible paths for estimating the
    correction vector:
    1. We find a 'matching' scan in the cache, *and* the drift estimation
    algorithm is able to reliably determine a correction vector (i.e. its
    fitting score is high enough). Here, we compute a new CorrectionInfo
    considering the drift we detected and the prior correction we had
    already applied during that scan.
    2. We do not find a 'matching' scan in the cache (or the fit was not
    good enough). In this case, we estimate a correction vector based on our
    currently estimated drift rate and the time elapsed.

    We should also clarify what 'matching' means when we find a match between
    the oldest scan and those in the cache. A scan pair is considered 'matched'
    if:
    - The intersection area of the scan's physical scan regions is sufficiently
    large (we use min_intersection_ratio for this).
    - The two scans are close enough in their spatial resolutions (we use
    min_spatial_res_ratio for this). If the spatial resolutions are too
    distinct, it is unlikely we will find matching keypoints between them
    (due to too-different signals).

    Note that in order to ensure we are comparing the appropriate scans, you
    must indicate the channel_id to consider. Only the channels that match
    channel_id are considered and compared.

    There are two approaches for matching scans: grabbing the latest scan in
    the cache that maches the new scan or grabbing the oldest. Grabbing the
    latest would work best if scans are frequent, such that any change in
    drift is minimal. It would also make sense if the surface being scanned
    is not constant. Grabbing the oldest would work best if scans are
    infrequent. If scans are infrequent, our estimated scan position may be
    reasonably incorrect from the reality. If we were comparing latest scans,
    one could run into a problem where we 'correct' only for the latest scan,
    which has actually drifted from our original position. These errors
    accumulate. For this setting to make any difference, the cache used must
    be different from the default (which only holds the last value).

    Additionally, this class in conjunction with DriftRescanner allows
    rescanning when the resulting scan is found to have drifted too far
    from the desired location. This is measured by the
    rescan intersection ratio (intersection area / scan area). When this ratio
    is above self.rescan_intersection_ratio, this CSCorrectedScheduler sends
    out the desired ScanParameters2d via its publisher. If the DriftRescanner's
    subscriber is listening to the same url as the publisher, it will receive
    it, log an EP_THERMAL_DRIFT problem, take control, and rerun the scan. Once
    the scan has run, it will release control and the experiment will continue.
    Note that this CSCorrectedScheduler also publishes ControlState and
    ScopeState messages, as these are necessary for DriftRescanner to function.

    Attributes:
        channel_id: str of the scan channel to consider when analyzing drift.
        drift_model: the DriftModel used to estimate a correction vector
            between two scans.
        total_corr_info: total CorrectionInfo, fed to IO nodes so they can
            correct for the TCS-SCS transform.
        update_weight: weight used to update total_corr_info with a new
            estimate.
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
        rescan_intersection_ratio: if the intersection area to scan ratio is
            below this value, we force a rescan. This thresohld allows us to
            ensure our scans have 'enough' of the data we desire.
        display_fit: visualize the fitting while it runs.
        grab_oldest_match: when trying to match prior scans to the latest
            match, whether we try to find the oldest match. If False, we look
            for the youngest match. Defaults to True.
        figure: figure used to visualize (if applicable).
    """

    SCAN_ID = cache_logic.CacheLogic.get_envelope_for_proto(scan_pb2.Scan2d())
    DEFAULT_CSV_ATTRIBUTES = csv.CSVAttributes('./drift_correction.csv')
    DEFAULT_MIN_INTERSECTION_RATIO = 0.5
    DEFAULT_MIN_SPATIAL_RES_RATIO = 0.25
    # This default fitting score is linked to RANSAC minimum residual threshold
    DEFAULT_MAX_FITTING_SCORE = drift.DEFAULT_RESIDUAL_THRESH_PERCENT
    DEFAULT_RESCAN_INTERSECTION_RATIO = 0.75
    DEFAULT_DISPLAY_FIT = True
    DEFAULT_GRAB_OLDEST = True

    CSV_FIELDS_V1 = ['timestamp', 'filename', 'pcs_to_scs_trans',
                     'pcs_to_scs_units', 'pcs_to_scs_rate',
                     'scan_matched']
    CSV_FIELDS_V2 = ['datetime', 'filename',
                     'corr_offset_x', 'corr_offset_y', 'corr_offset_units',
                     'corr_rate_x', 'corr_rate_y', 'corr_rate_units',
                     'scan_matched']
    DEFAULT_SPAWN_DELAY_S = 5.0  # Slow startup.
    DEFAULT_BEAT_PERIOD_S = 3.0  # Slow beat.

    def __init__(self, channel_id: str,
                 drift_model: drift.DriftModel | None = None,
                 csv_attribs: csv.CSVAttributes = DEFAULT_CSV_ATTRIBUTES,

                 min_intersection_ratio: float = DEFAULT_MIN_INTERSECTION_RATIO,
                 min_spatial_res_ratio: float = DEFAULT_MIN_SPATIAL_RES_RATIO,
                 max_fitting_score: float = DEFAULT_MAX_FITTING_SCORE,
                 update_weight: float = DEFAULT_UPDATE_WEIGHT,
                 rescan_intersection_ratio: float =
                 DEFAULT_RESCAN_INTERSECTION_RATIO,
                 display_fit: bool = DEFAULT_DISPLAY_FIT,
                 grab_oldest_match: bool = True, **kwargs):
        """Initialize our correction scheduler."""
        self.channel_id = channel_id.upper()
        self.drift_model = (drift.create_drift_model() if drift_model is None
                            else drift_model)
        self.csv_attribs = csv_attribs
        self.min_intersection_ratio = min_intersection_ratio
        self.min_spatial_res_ratio = min_spatial_res_ratio
        self.max_fitting_score = max_fitting_score
        self.update_weight = update_weight
        self.rescan_intersection_ratio = rescan_intersection_ratio
        self.rerun_scan = False
        self.rerun_scan_params = None

        self.total_corr_info = None

        self.display_fit = display_fit
        self.grab_oldest_match = grab_oldest_match
        self.figure = (plt.figure(layout=PLT_LAYOUT) if self.display_fit
                       else None)

        # Display window on startup, so the user knows to expect it.
        if self.display_fit:
            plt.show(block=False)

        # Warn user if using default fitting score and not RANSAC fitting
        if (self.max_fitting_score == self.DEFAULT_MAX_FITTING_SCORE and
                self.drift_model.fitting != drift.FittingMethod.RANSAC):
            logger.warning('Using default fitting score for fitting method '
                           f'{drift_model.fitting} (i.e. not RANSAC). This '
                           'is probably too low!')

        # Create our wrapper router and cache
        kwargs[scheduler.ROUTER_KEY] = CSCorrectedRouter.from_parent(
            kwargs[scheduler.ROUTER_KEY])
        kwargs[scheduler.CACHE_KEY] = CSCorrectedCache.from_parent(
            kwargs[scheduler.CACHE_KEY])

        super().__init__(**kwargs)

        # In order to update our logic, we bind update to the
        # cache receiving a message (i.e. updates from the Microscope).
        self.pubsubcache.bind_to(self.cache_received_message)

        csv.init_csv_file(self.csv_attribs, self.CSV_FIELDS_V2)

    def _get_drift_snapshot(self, new_scan: scan_pb2.Scan2d
                            ) -> correction.DriftSnapshot | None:
        """Estimate drift for the new scan."""
        if self.figure is not None:
            self.figure.clear()  # Clear figure before showing

        matched_scans = proto_geo.get_intersections(
            self._get_scans_from_cache(), new_scan,
            self.min_intersection_ratio, self.min_spatial_res_ratio)

        if len(matched_scans) == 0:
            return None

        # If grabbing youngest, flip from ascending to descending timestamp
        # order.
        if not self.grab_oldest_match:
            matched_scans = matched_scans[::-1]

        snapshot = None
        for matched_scan in matched_scans:
            snapshot = correction.compute_drift_snapshot(
                matched_scan, new_scan, self.drift_model,
                self.max_fitting_score, self.display_fit,
                self.figure)
            if snapshot is not None:
                break

        if self.display_fit:
            plt.show(block=False)
        return snapshot

    def _get_scans_from_cache(self) -> list[scan_pb2.Scan2d]:
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

    # ----- Drift mapping stuff ----- #
    def cache_received_message(self, proto: Message):
        """Analyze scans whenever the cache receives them."""
        if (isinstance(proto, scan_pb2.Scan2d) and
                self.channel_id in proto.channel.upper()):
            self.update(proto)

        if ((isinstance(proto, control_pb2.ControlState) or
                isinstance(proto, scan_pb2.ScopeStateMsg)) and
                self.publisher is not None):
            self.publisher.send_msg(proto)

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
        corrected_scan = self._get_corrected_scan(new_scan)
        scan_matched = self._update_correction(new_scan, corrected_scan)
        self._save_metadata(corrected_scan, scan_matched)
        self._update_io()

    def _get_corrected_scan(self, new_scan: scan_pb2.Scan2d) -> scan_pb2.Scan2d:
        """Convert current scan from TCS to SCS."""
        return cs_correct_proto(new_scan, self.total_corr_info,
                                self.update_weight,
                                new_scan.timestamp.ToDatetime(
                                    dt.timezone.utc))

    def _update_correction(self, new_scan: scan_pb2.Scan2d,
                           corrected_scan: scan_pb2.Scan2d) -> bool:
        """Estimate snapshot and update correction accordingly.

        Args:
            new_scan: scan_pb2 in tip coordinate system (TCS).
            corrected_scan: scan_pb2 in scan coordinate system (SCS).

        Returns:
            Whether or not we were able to estimate a snapshot this time step.
        """
        snapshot = self._get_drift_snapshot(corrected_scan)
        self._update_curr_corr_info(corrected_scan, snapshot)

        # Determine if the drift was too much and we must redo our scan.
        # Note that we feed the original scan, as we will update it with
        # the latest correction info.
        self._determine_redo_scan(new_scan, corrected_scan)
        return snapshot is not None

    def _save_metadata(self, corrected_scan: scan_pb2.Scan2d,
                       scan_matched: bool):
        """Save current correction info in CSV file."""
        row_vals = get_metadata_row_v2(corrected_scan, self.total_corr_info,
                                       scan_matched)
        csv.save_csv_row(self.csv_attribs, self.CSV_FIELDS_V2,
                         row_vals)

    def _update_curr_corr_info(self, new_scan: scan_pb2.Scan2d,
                               snapshot: correction.DriftSnapshot | None):
        """Update the current CorrectionInfo based on the new incoming scan.

        Given a new scan and knowledge of whether we matched a scan to it from
        our history, we update the current CorrectionInfo. If there was a
        match, we can simply grab the latest DriftSnapshot. If not, we
        estimate a correction vector based on the history.
        """
        if snapshot is not None:
            logger.trace('Scan matched.')
            corr_info = correction.estimate_correction_from_snapshot(
                snapshot, self.total_corr_info)
            logger.trace(f'delta corr_info: {corr_info}')
        else:
            logger.trace('No match. Estimating from prior info.')
            corr_info = correction.estimate_correction_no_snapshot(
                self.total_corr_info, new_scan.timestamp.ToDatetime(
                    dt.timezone.utc))
            logger.trace(f'delta corr_info: {corr_info}')
        new_tot_corr_info = correction.update_total_correction(
            self.total_corr_info, corr_info, self.update_weight)

        # Drift has changed if we received our first CorrectionInfo
        # or if both exist and their vectors are different.
        first_corr_info = (new_tot_corr_info is not None and
                           self.total_corr_info is None)
        both_exist = (new_tot_corr_info is not None and
                      self.total_corr_info is not None)

        drift_has_changed = (first_corr_info or
                             (both_exist and
                              new_tot_corr_info != self.total_corr_info))
        self.total_corr_info = new_tot_corr_info

        # Notify logger if correction vec has changed
        if drift_has_changed:
            logger.debug('The TCS-to-SCS correction vector has changed'
                         f': {self.total_corr_info.vec} '
                         f'{self.total_corr_info.unit}.')
            logger.debug(f'With corr rate: {self.total_corr_info.rate} '
                         f'{self.total_corr_info.unit} / s.')

    def _update_io(self):
        """Update IO nodes with latest CorrectionInfo.

        Inform our IO nodes of the latest CorrectionInfo, so they may use
        it to 'correct' the coordinate system accordingly.
        """
        self.router.update_correction_info(self.total_corr_info,
                                           self.update_weight)
        self.pubsubcache.update_correction_info(self.total_corr_info,
                                                self.update_weight)

    def run_per_loop(self):
        """Override to update figures and handle scan rerunning."""
        super().run_per_loop()
        self._update_ui()

    def _update_ui(self):
        if self.figure is not None:
            self.figure.canvas.draw_idle()
            self.figure.canvas.flush_events()

    # ----- Scan rerunning logic ----- #
    def _determine_redo_scan(self, uncorrected_scan: scan_pb2.Scan2d,
                             prior_corrected_scan: scan_pb2.Scan2d):
        """Determine if we need to redo the scan or not."""
        if self.publisher is None:  # Cannot send params if no publisher!
            logger.warning('Unable to send scan rerun info because we have no '
                           'publisher.')
            return

        true_scan = cs_correct_proto(uncorrected_scan,
                                     self.total_corr_info,
                                     self.update_weight,
                                     uncorrected_scan.timestamp.ToDatetime(
                                         dt.timezone.utc))
        true_rect = true_scan.params.spatial.roi
        expected_rect = prior_corrected_scan.params.spatial.roi
        area_ratio = proto_geo.intersection_ratio(true_rect, expected_rect)

        if area_ratio < self.rescan_intersection_ratio:
            # Tell our scan handler to rescan prior region.
            logger.warning('True vs. expected scans are too far apart. '
                           'Sending scan params out via our publisher.')
            scs_params = self.router._last_scan_params
            if scs_params is None:
                logger.error('Could not send out last scan params for a '
                             'rescan because our stored params are None!')
            else:
                self.publisher.send_msg(scs_params)

    def _handle_shutdown(self):
        """Override to send kill via publisher (if provided)."""
        if self.router.shutdown_was_requested and self.publisher is not None:
            self.publisher.send_kill_signal()
        super()._handle_shutdown()


def get_metadata_row_v1(scan: scan_pb2.Scan2d,
                        corr_info: correction.CorrectionInfo | None,
                        estimated_from_vec: bool) -> [str]:
    """Get metadata row for CSV logging current state (V1)."""
    row_vals = [scan.timestamp.seconds,
                scan.filename,
                corr_info.vec if corr_info is not None else None,
                corr_info.unit if corr_info is not None else None,
                corr_info.rate if corr_info is not None else None,
                estimated_from_vec]
    return row_vals


def get_metadata_row_v2(scan: scan_pb2.Scan2d,
                        corr_info: correction.CorrectionInfo | None,
                        estimated_from_vec: bool) -> [str]:
    """Get metadata row for CSV logging current state (V2)."""
    row_vals = [scan.timestamp.ToDatetime(dt.timezone.utc).isoformat(),
                scan.filename,
                corr_info.vec[0] if corr_info is not None else None,
                corr_info.vec[1] if corr_info is not None else None,
                corr_info.unit if corr_info is not None else None,
                corr_info.rate[0] if corr_info is not None else None,
                corr_info.rate[1] if corr_info is not None else None,
                corr_info.unit + '/s' if corr_info is not None else None,
                estimated_from_vec]
    return row_vals
