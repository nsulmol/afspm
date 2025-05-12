"""Holds logic for a drift-corrected scheduler.

The scheduler in here will use logic from drift.py in order to automatically
convert from the piezo coordinate system (PCS) to the 'true' sample coordinate
system (SCS).  The idea is to track drift and from it determine the correction
vector needed to perform on any incoming request or outgoing message such that
it is always in the estimated SCS.
"""

from dataclasses import dataclass
from collections import deque
import datetime as dt
import logging
import numpy as np
import xarray as xr
from google.protobuf.message import Message
from google.protobuf.message_factory import GetMessageClass

from . import drift
from ..microscope import scheduler
from ...utils import csv
from ...utils import array_converters as ac
from ...utils.units import convert_list
from ...io.control import router
from ...io.pubsub import cache
from ...io.pubsub.logic import cache_logic

from ...io.protos.generated import control_pb2
from ...io.protos.generated import scan_pb2
from ...io.protos.generated import spec_pb2
from ...io.protos.generated import geometry_pb2


logger = logging.getLogger(__name__)


DEFAULT_EMPTY_DATETIME = dt.datetime(1, 1, 1)
ANGLE_FIELD = 'angle'  # Used to check if an angle is set in scan_params


@dataclass
class CorrectionInfo:
    """Holds correction vector and timing information between two scans."""

    dt1: dt.datetime  # Time of first scan.
    dt2: dt.datetime  # Time of second scan.
    vec: np.ndarray  # Translation vector to correct second to first scan CS.
    units: str  # Translation vector units


# kwarg keys for Scheduler constructor
CACHE_KEY = 'pubsubcache'
ROUTER_KEY = 'router'


def correct_spatial_aspects(proto: scan_pb2.SpatialAspects,
                            correction_vec: np.ndarray,
                            correction_units: str,
                            ) -> scan_pb2.SpatialAspects:
    """Correct spatial aspects given a correction vector."""
    # NOTE: Rotation angle should not affect this.
    # From skimage doc (where we get our trans vec),
    # the keypoints we receive are (row, col), i.e. (y, x).
    # So we follow that here.
    orig_tl = np.ndarray([proto.spatial.roi.top_left.y,
                          proto.spatial.roi.top_left.x])

    # Update correction vec to proto units
    roi_units = (proto.spatial.length_units,
                 proto.spatial.length_units)
    correction_units = (correction_units, correction_units)
    correction_vec = np.array(convert_list(correction_vec.tolist(),
                                           correction_units, roi_units))

    corrected_tl = orig_tl + correction_vec
    proto.spatial.roi.top_left = geometry_pb2.Point2d(
        corrected_tl.x, corrected_tl.y)
    return proto


def correct_probe_position(proto: spec_pb2.ProbePosition,
                           correction_vec: np.ndarray,
                           correction_units: str,
                           ) -> spec_pb2.ProbePosition:
    """Correct probe position given a correction vector."""
    # From skimage doc (where we get our trans vec),
    # the keypoints we receive are (row, col), i.e. (y, x).
    # So we follow that here.
    orig_pos = np.ndarray([proto.point.y, proto.point.x])

    # Update correction vec to proto units
    roi_units = (proto.units, proto.units)
    correction_units = (correction_units, correction_units)
    correction_vec = np.array(convert_list(correction_vec.tolist(),
                                           correction_units, roi_units))

    corrected_pos = orig_pos + correction_vec

    proto.point = geometry_pb2.Point2d(
        corrected_pos.x, corrected_pos.y)
    return proto


def cs_correct_proto(proto: Message, correction_vec: np.ndarray,
                     correction_units: str,) -> Message:
    """Recursively go through a protobuf, correcting CS-based fields.

    When we find fields that should be corrected for an updated coordinate
    system, we fix them.
    """
    logger.trace(f'Proto before correcting: {proto}')
    constructor = GetMessageClass(proto.DESCRIPTOR)
    vals_dict = {}
    # NOTE: any new protos that have spatial fields should be added here!
    for field in proto.DESCRIPTOR.fields:
        val = getattr(proto, field.name)
        new_val = None
        if isinstance(val, Message):
            new_val = cs_correct_proto(val, correction_vec, correction_units)
        elif isinstance(val, scan_pb2.SpatialAspects):
            new_val = correct_spatial_aspects(val, correction_vec,
                                              correction_units)
        elif isinstance(val, spec_pb2.ProbePosition):
            new_val = correct_probe_position(val, correction_vec,
                                             correction_units)

        if new_val:
            vals_dict[field.name] = new_val

    partial_proto = constructor(**vals_dict)
    proto.MergeFrom(partial_proto)
    logger.trace(f'Proto after correcting: {proto}')
    return proto


class CSCorrectedRouter(router.ControlRouter):
    """Corrects CS data before it is sent out via the router."""

    def __init__(self):
        """Init - this class requires usage of from_parent."""
        self._correction_vec = np.zeros((2,))
        self._correction_units = None

    def _handle_send_req(self, req: control_pb2.ControlRequest,
                         proto: Message) -> (control_pb2.ControlResponse,
                                             Message | int | None):
        """Override to correct CS data before sending out."""
        # Correct CS data of proto
        proto = cs_correct_proto(proto, self._correction_vec,
                                 self._correction_units)

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

    def update_correction_vec(self, correction_vec: np.ndarray,
                              correction_units: str):
        """Update our correction vector.

        The correction vector is PCS -> SCS. The router is receiving
        requests from components in the SCS, to be sent to the Microscope.
        Thus, we want to go SCS -> PCS, which means we must invert our
        translation vector.
        """
        self._correction_vec = -correction_vec
        self._correction_units = correction_units


class CSCorrectedCache(cache.PubSubCache):
    """Corrects CS data before it is sent out to subscribers."""

    def __init__(self, **kwargs):
        """Init - this class requires usage of from_parent."""
        self._correction_vec = np.zeros((2,))
        self._correction_units = None
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

        # Correct CS data of proto
        proto = cs_correct_proto(proto, self._correction_vec,
                                 self._correction_units)

        # Save in cache / send out
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

    def update_correction_vec(self, correction_vec: np.ndarray,
                              correction_units: str):
        """Update our correction vector.

        The correction vector is PCS -> SCS. The PubSubCache receives requests
        from the Microscope in PCS, and must convert it to SCS (what components
        expect). Thus, we use PCS -> SCS< which is the correction vector
        without modifications.
        """
        self._correction_vec = correction_vec
        self._correction_units = correction_units


class CSCorrectedScheduler(scheduler.MicroscopeScheduler):
    """Corrects coordinate system data in addition to being a scheduler.

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
    tells us the translation needed to convert from PCS to SCS. We can then use
    this correction vector to 'correct' published data from the Microscope into
    the SCS. We can also use the inverse transform to 'correct' requests to the
    Microscope (which are in the SCS) to the PCS.

    Our DriftModel will only detect correction vectors between two
    independent scans that are a certain time delta apart. In order to convert
    to the true SCS, we need to add up the various drifts that have occurred
    since the beginning of the experiment. To do so, we maintain a list of
    CorrectionInfo over time. This vector will be 'condensed' over time, for
    the cases where we have time intersections of various CorrectionInfo. For
    post-experiment usage, we save a csv file containing the relative and
    absolute correction vectors computed over time at filepath.

    In order to determine correction vectors, the DriftModel needs to compare
    each current scan with a prior scan that intersects over the same PCS. To
    do so, we consider the scans available in the current cache *before*
    updating. Thus, it is important that the cache is set up such that we
    are likely to have a prior scan to compare to.

    It is also possible that there are multiple CorrectionInfos corresponding
    to a given time interval. For example, imagine an experiment alternates
    between scanning a 'large' scan and then 2 'small' scans in sub-regions of
    the larger scan. For each 'small' scan, we can estimate a correction vector
    between the scan and the portion of that scan region in the larger scan.
    Similarly, once we get to the second large scan, we can estimate a
    correction vector between the previous large scan and the current one.
    However, there are also now 2 correction vectors that intersect with the
    time period between the first and second large scans.

    Because of this, we:
    - estimate a correction vector from all CorrectionInfos in a given time
    subset.
    - condense CorrectionInfos in our history with intersecting time, to
    minimize the amount of data we are storing.


    Attributes:
        drift_model: the DriftModel used to estimate a correction vector
            between two scans.
        correction_infos: a deque of CorrectionInfo that has been collected
            over time.
        current_correction_vec: currently estimated correction vector to go
            from PCS -> SCS.
        current_correction_units: units associated with estimated correction
            vector.
        filepath: path where we save our csv file containing all correction
            data associated to scans.
        min_intersection_ratio: minimum intersection area to scan ratio to
            accept two scans as intersecting. The scan area in our
            numerator is that of the newer scan.
        min_sptaial_res_ratio: minimum spatial rseolution ratio between two
            scans to accept them as matching. If the two scans have vastly
            different spatial resolutions, it is unlikely we will find
            keypoint matches!
    """

    SCAN_ID = cache_logic.CacheLogic.get_envelope_for_proto(scan_pb2.Scan2d())
    DEFAULT_MIN_INTERSECTION_RATIO = 0.5
    DEFAULT_MIN_SPATIAL_RES_RATIO = 0.5
    DEFAULT_HISTORY_LENGTH = 5

    DEFAULT_CSV_ATTRIBUTES = csv.CSVAttributes('./drift_correction.csv')

    CSV_FIELDS = ['timestamp', 'filename', 'pcs_to_scs_trans',
                  'estimated_from_drift?']

    def __init__(self, drift_model: drift.DriftModel | None = None,
                 csv_attribs: csv.CSVAttributes = DEFAULT_CSV_ATTRIBUTES,
                 min_intersection_ratio: float = DEFAULT_MIN_INTERSECTION_RATIO,
                 min_spatial_res_ratio: float = DEFAULT_MIN_SPATIAL_RES_RATIO,
                 correction_history_length: int = DEFAULT_HISTORY_LENGTH,
                 **kwargs):
        """Initialize our correction scheduler."""
        self.drift_model = (drift.create_drift_model() if drift_model is None
                            else drift_model)
        self.csv_attribs = csv_attribs
        self.min_intersection_ratio = min_intersection_ratio
        self.min_spatial_res_ratio = min_spatial_res_ratio
        self.correction_infos = deque(maxlen=correction_history_length)
        self.current_correction_vec = np.array([0.0, 0.0])
        self.current_correction_units = None

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
                          correction_vec: np.ndarray,
                          estimated_from_vec: bool) -> [str]:
        row_vals = [scan.timestamp.seconds,
                    scan.filename,
                    correction_vec,
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
        map(concatenated_scans.extend, scan_deques)
        return concatenated_scans

    def _update_correction_infos(self, new_scan: scan_pb2.Scan2d
                                 ) -> bool:
        """Update CorrectionInfos history based on the new incoming scan."""
        matched_scan = get_latest_intersection(
            self._get_scans_from_cache(), new_scan,
            self.min_intersection_ratio, self.min_spatial_res_ratio)

        scan_was_matched = matched_scan is not None
        if scan_was_matched:
            del_corr_info = compute_correction_info(
                matched_scan, new_scan, self.drift_model)
            del_corr_info.vec += self.current_correction_vec
            self.correction_infos.append(del_corr_info)
        return scan_was_matched

    def _update_correction_vec(self, new_scan: scan_pb2.Scan2d,
                               scan_was_matched: bool):
        """Update the correction vector based on the new incoming scan.

        Given a new scan and knowledge of whether we matched a scan to it from
        our history, we update the current correction vector. If there was a
        match, we can simply grab the latest CorrectionInfo. If not, we
        estimate a correction vector based on the history.
        """
        if len(self.correction_infos) == 0:
            return  # Early return, there is nothing to correct!

        if scan_was_matched:
            new_correction_vec = self.correction_infos[-1].vec
        else:
            new_correction_vec = estimate_correction_vec(
                self.correction_infos, self.current_correction_vec,
                new_scan.timestamp)

        # Notify logger if correciton vec has changed
        if np.all(np.isclose(new_correction_vec, self.current_correction_vec)):
            logger.info('The PCS-to-SCS correction vector has changed'
                        f': {new_correction_vec}.')

        # Update internal vec + units (assuming consistent units, grabbing
        # latest)
        self.current_correction_vec = new_correction_vec
        self.current_correction_units = self.correction_infos[-1].units

    def _update_io(self):
        """Update IO nodes with latest correction vec.

        Inform our IO nodes of the latest correction vector, so they may use
        it to 'correct' the coordinate system accordingly.
        """
        self.pubsubcache.update_correction_vec(self.current_correction_vec,
                                               self.current_correction_units)
        self.router.update_correction_vec(self.current_correction_vec,
                                          self.current_correction_units)

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
        scan_was_matched = self._update_correction_infos(new_scan)
        self._update_correction_vec(new_scan, scan_was_matched)

        # TODO: What if the drift is too much? At what point do we take over and
        # redo the scan?

        row_vals = self._get_metadata_row(new_scan, self.current_correction_vec,
                                          not scan_was_matched)
        csv.save_csv_row(self.csv_attribs, self.CSV_FIELDS,
                         row_vals)
        self._update_io()


def rect_intersection(a: geometry_pb2.Rect2d, b: geometry_pb2.Rect2d
                      ) -> geometry_pb2.Rect2d:
    """Compute intersection of two Rect2ds."""
    if a.HasField(ANGLE_FIELD) or b.HasField(ANGLE_FIELD):
        msg = ('Rect2d passed to rect_intersection with angle field. '
               'This is not currently supported.')
        logger.error(msg)
        raise ValueError(msg)

    a_x1 = a.top_left.x
    a_x2 = a.top_left.x + a.size.x
    a_y1 = a.top_left.y
    a_y2 = a.top_left.y + a.size.y

    b_x1 = b.top_left.x
    b_x2 = b.top_left.x + b.size.x
    b_y1 = b.top_left.y
    b_y2 = b.top_left.y + b.size.y

    x1 = max(min(a_x1, a_x2), min(b_x1, b_x2))
    y1 = max(min(a_y1, a_y2), min(b_y1, b_y2))

    x2 = min(max(a_x1, a_x2), max(b_x1, b_x2))
    y2 = min(max(a_y1, a_y2), max(b_y1, b_y2))

    logger.trace(f'x1: {x1}, x2: {x2}, y1: {y1}, y2: {y2}')

    if x2 < x1 or y2 < y1:
        return geometry_pb2.Rect2d()  # All 0s, no intersection!

    tl = geometry_pb2.Point2d(x=x1, y=y1)
    size = geometry_pb2.Size2d(x=x2 - x1, y=y2 - y1)
    return geometry_pb2.Rect2d(top_left=tl, size=size)


def rect_area(rect: geometry_pb2.Rect2d) -> float:
    """Compute area of a Rect2d."""
    return rect.size.x * rect.size.y


def time_intersection(a: CorrectionInfo, b: CorrectionInfo
                      ) -> (dt.datetime, dt.datetime):
    """Compute temporal intersection of two CorrectionInfos."""
    dt1 = max(a.dt1, b.dt1)
    dt2 = min(a.dt2, b.dt2)
    if dt1 <= dt2:
        return (dt1, dt2)
    return (DEFAULT_EMPTY_DATETIME, DEFAULT_EMPTY_DATETIME)


def time_intersection_delta(a: CorrectionInfo, b: CorrectionInfo
                            ) -> dt.timedelta:
    """Compute temporal intersection of two Correction infos, return delta."""
    dt1, dt2 = time_intersection(a, b)
    return dt2 - dt1


def intersection_ratio(a: geometry_pb2.Rect2d,
                       b: geometry_pb2.Rect2d) -> float:
    """Compute the intersection ratio between two Rect2ds.

    To do this, we first find the intersection rect between the two. To get a
    reasonable ratio, we divide the area of this intersection by the *smaller*
    of the two rectangles. We choose this because we are interested in the
    intersection data only, so we do not really care if one of the rectangles
    corresponds to a much larger region. Choosing the larger of the too would
    make this ratio less useful (potentially), as the values may end up vary
    small. Choosing based on the order the rectangles are given would cause
    it to behave unexpectedly if the user chose the order wrong (for their
    purposes).

    Args:
        a: first Rect2d.
        b: second Rect2d.

    Returns:
        Ratio of the area of the intersection and the area of the smaller of
        the two provided rects.
    """
    smallest_area = sorted([rect_area(a), rect_area(b)])[0]
    inter_area = rect_area(rect_intersection(a, b))
    return inter_area / smallest_area


def spatial_resolution(a: scan_pb2.Scan2d) -> float:
    """Compute the spatial resolution of a scan.

    We grab the mean of the two spatial resolutions.
    """
    spatials = np.array([a.params.spatial.roi.size.x,
                         a.params.spatial.roi.size.y])
    resolutions = np.array([a.params.data.shape.x, a.params.data.shape.y])
    spatial_resolutions = spatials / resolutions
    return np.mean(spatial_resolutions)


def spatial_resolution_ratio(a: scan_pb2.Scan2d,
                             b: scan_pb2.Scan2d) -> float:
    """Compute the ratio of the spatial resolutions of the two scans.

    We define the ratio as the smaller over the larger.
    This choice is arbitrary, but means the ratio is in the range [0, 1].
    """
    spatial_resolutions = [spatial_resolution(a), spatial_resolution(b)]
    return min(spatial_resolutions) / max(spatial_resolutions)


def get_latest_intersection(scans: list[scan_pb2.Scan2d],
                            new_scan: scan_pb2.Scan2d,
                            min_intersection_ratio: float,
                            min_spatial_res_ratio: float,
                            ) -> scan_pb2.Scan2d | None:
    """Get latest intersection between scans and a new_scan.

    This method searches scans for a scan that has a 'sufficiently close'
    intersection with new_scan. By 'sufficiently close' we mean:
    1. The intersection ratio between the two is not too low; and
    2. The spatial resolution ratio between two is not too low.

    For (1): if the intersection between the two is too small, analyzing
    the two scans for matching descriptors is not realistic.
    For (2): if the spatial resolutions are too different, we simply won't find
    matching features.

    Args:
        scans: list of scans to compare new_scan to.
        new_scan: the scan we are matching.
        min_intersection_ratio: the minimum ratio to be considered a
            match.
        min_spatial_res_ratio: the minimum spatial resolution ratio to
            be considered a match.

    Returns:
        The most recent matching scan or None if none found.
    """
    intersect_scans = []
    for scan in scans:
        inter_ratio = intersection_ratio(scan.params.spatial.roi,
                                         new_scan.params.spatial.roi)
        spatial_res_ratio = spatial_resolution_ratio(scan, new_scan)
        if (inter_ratio >= min_intersection_ratio and
                spatial_res_ratio >= min_spatial_res_ratio):
            intersect_scans.append(scan)

    if not intersect_scans:
        return None

    intersect_scans.sort(key=lambda scan: scan.timestamp)
    return intersect_scans[-1]  # Last value is latest timestamp


def extract_patch(da: xr.DataArray,
                  rect: geometry_pb2.Rect2d,
                  ) -> (xr.DataArray, xr.DataArray):
    """Extract intersection patch from DataArray.

    Args:
        da: DataArray.
        rect: intersection rectangle.

    Returns:
        Associated patch, as a DataArray.
    """
    xs = slice(rect.top_left.x, rect.top_left.x + rect.size.x)
    ys = slice(rect.top_left.y, rect.top_left.y + rect.size.y)
    return da.sel(x=xs, y=ys)


def extract_and_scale_patches(da1: xr.DataArray,
                              da2: xr.DataArray,
                              rect: geometry_pb2.Rect2d,
                              ) -> (xr.DataArray, xr.DataArray):
    """Extract intersection patches from images, matching spatial res.

    Extract patches from da1 and da2 corresponding to rect, and update
    them so they have matching spatial resolutions. For the latter, we
    update da1 so it is the same spatial resolution as da2.

    Args:
        da1: First DataArray.
        da2: Second DataArray.
        rect: intersection rectangle.

    Returns:
        Tuple of associated patches (patch_da1, patch_da2).
    """
    patches = [extract_patch(da1, rect), extract_patch(da2, rect)]
    patches[0] = patches[0].interp_like(patches[1])
    return tuple(patches)


def compute_correction_info(scan1: scan_pb2.Scan2d,
                            scan2: scan_pb2.Scan2d,
                            drift_model: drift.DriftModel
                            ) -> CorrectionInfo:
    """Compute CorrectionInfo between two scans.

    Given two scans, estimate a transform for scan2 to be transformed to be in
    the same coordinate system as scan1. We return a CorrectionInfo instance,
    which holds the datetimes of the two scans and the correction vector
    (translation) from scan2 to scan1. Note that this assumes we are only
    interested in the translation component of the transform!

    Args:
        scan1: first scan_pb2.Scan2d.
        scan2: second scan_pb2.Scan2d.
        drift_model: DriftModel used to estimate the transform between scan2
            and scan1.

    Returns:
        Computed CorrectionInfo.
    """
    da1 = ac.convert_scan_pb2_to_xarray(scan1)
    da2 = ac.convert_scan_pb2_to_xarray(scan2)

    # Get intersection patches (scaled to scan2, as the transform is
    # for da2 to move to da1's position).
    inter_rect = rect_intersection(scan1, scan2)
    patch1, patch2 = extract_and_scale_patches(da1, da2, inter_rect)

    transform, score = drift.estimate_transform(drift_model, da1, da2)
    trans, units = drift.get_translation(da2, transform)

    correction_info = CorrectionInfo(scan1.timestamp.ToDatetime(),
                                     scan2.timetsamp.ToDatetime(),
                                     np.ndarray(trans),
                                     units)
    return correction_info


def estimate_correction_vec(correction_infos: list[CorrectionInfo],
                            current_correction_vec: np.ndarray,
                            time: dt.datetime) -> np.ndarray:
    """Estimate a correction vector when no scan match was found.

    This method updates the current correction vector considering the latest
    history of CorrectionInfos. It uses the end time of the last CorrectionInfo
    as the time for which current_correction_vec exists, meaning we still need
    to estimate a delta correction vector to account for the time between then
    and the latest scan (which occured at the input argument time).

    It then calculates the drift rates (vec / time) of all of the
    CorrectionInfos in our history and averages them to get a drift rate
    estimate. Lastly, it computes the delta vector between the latest
    correction vector and our current time and adds this to the correction vec,
    which is returned.

    Args:
        correction_infos: the history of CorrectionInfos, to be used to
            estimate the drift rate and determine the time of the current
            correction vec.
        current_correction_vec: the last used correction vector going from
            PCS-SCS.
        time: the time when the latest scan occurred (for which we could not
            find a match).

    Returns:
        np.ndarray of the updated correction vector.
    """
    unit_dist = correction_infos[-1].units
    dt1 = correction_infos[-1].dt2
    dt2 = time

    logger.trace(f'dt1: {dt1}')
    logger.trace(f'dt2: {dt2}')

    drift_rates = []
    for info in correction_infos:
        units = (info.units, info.units)
        vec = convert_list(info.vec, units,
                           (unit_dist, unit_dist))
        vec = np.array(vec)
        drift_rate = vec / (info.dt2 - info.dt1).total_seconds()
        drift_rates.append(drift_rate)
        logger.trace(f'vec: {vec}')
        logger.trace(f'drift rate: {drift_rate}')
        logger.trace(f'units: {units}')

    avg_drift_rate = np.mean(np.array(drift_rates), axis=0)
    logger.trace(f'avg_drift_rate: {avg_drift_rate}')
    del_correction_vec = avg_drift_rate * (dt2 - dt1).total_seconds()
    logger.trace(f'del_correction_vec: {del_correction_vec}')

    correction_vec = current_correction_vec + del_correction_vec
    logger.trace(f'current_correction_vec: {current_correction_vec}')
    return correction_vec
