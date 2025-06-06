"""Methods for estimating corrections between scans."""

from dataclasses import dataclass, field
import datetime as dt
import logging

import numpy as np
import xarray as xr
import matplotlib.pyplot as plt

from . import drift
from ...utils import array_converters as ac
from ...utils import proto_geo

from ...io.protos.generated import scan_pb2
from ...io.protos.generated import geometry_pb2


logger = logging.getLogger(__name__)


DEFAULT_EMPTY_DATETIME = dt.datetime(1, 1, 1)

NO_VEC = np.array([0.0, 0.0])
DEFAULT_UNIT = None  # No unit


@dataclass
class CorrectionInfo:
    """Holds information necessary to correct for drift."""

    curr_dt: dt.datetime = None
    vec: np.ndarray = field(default_factory=lambda: NO_VEC)
    drift_rate: np.ndarray = field(default_factory=lambda: NO_VEC)
    unit: str | None = DEFAULT_UNIT


@dataclass
class DriftSnapshot:
    """Holds info on the drift estimated between two scans."""

    dt1: dt.datetime = None  # Time of first scan.
    dt2: dt.datetime = None  # Time of second scan.
    # Translation vector to correct second to first scan CS.
    vec: np.ndarray = field(default_factory=lambda: NO_VEC)
    unit: str | None = DEFAULT_UNIT  # Translation vector unit


def compute_drift_snapshot(scan1: scan_pb2.Scan2d,
                           scan2: scan_pb2.Scan2d,
                           drift_model: drift.DriftModel,
                           max_score: float,
                           display_fit: bool = False,
                           figure: plt.Figure = None
                           ) -> DriftSnapshot | None:
    """Compute DriftSnapshot between two scans.

    Given two scans, estimate a transform for scan2 to be transformed to be in
    the same coordinate system as scan1. We return a DriftSnapshot instance,
    which holds the datetimes of the two scans and the correction vector
    (translation) from scan2 to scan1. Note that this assumes we are only
    interested in the translation component of the transform!

    Args:
        scan1: first scan_pb2.Scan2d.
        scan2: second scan_pb2.Scan2d.
        drift_model: DriftModel used to estimate the transform between scan2
            and scan1.
        max_score: maximum fitting score to be considered a successful match.
            Note that a 0 is an ideal fit and a 1 the worst fit in this scoring
            logic.
        display_fit: whether or not to display fitting.

    Returns:
        Computed DriftSnapshot, or None on failure to fit (e.g. the fitting
        score is too low).
    """
    da1 = ac.convert_scan_pb2_to_xarray(scan1)
    da2 = ac.convert_scan_pb2_to_xarray(scan2)

    # Get intersection patches (scaled to scan2, as the transform is
    # for da2 to move to da1's position).
    inter_rect = proto_geo.rect_intersection(scan1.params.spatial.roi,
                                             scan2.params.spatial.roi)

    patch1, patch2, scale = extract_and_scale_patches(da1, da2,
                                                      inter_rect)

    transform, score = drift.estimate_transform(drift_model, patch1, patch2,
                                                display_fit,
                                                figure=figure)

    # NOTE: We are doing -trans because we want to *correct* for the drift
    # detected.
    if score <= max_score:
        trans, units = drift.get_translation(da2, transform)
        trans *= 1 / scale  # Update the transform given the scaling
        drift_snapshot = DriftSnapshot(
            scan1.timestamp.ToDatetime(dt.timezone.utc),
            scan2.timestamp.ToDatetime(dt.timezone.utc),
            -trans,
            units)  # Units from da2
        return drift_snapshot
    return None


def get_drift_rate(vec: np.ndarray, dt1: dt.datetime, dt2: dt.datetime
                   ) -> np.ndarray:
    """Estimate drift rate from vector and timestamps."""
    if dt2 is None or dt1 is None:
        return NO_VEC

    try:
        return vec / (dt2 - dt1).total_seconds()
    except ZeroDivisionError:
        return NO_VEC


def estimate_correction_vec(drift_rate: np.ndarray,
                            dt1: dt.datetime, dt2: dt.datetime) -> np.ndarray:
    """Estimate a correction vector given a drift rate and two DateTimes.

    This is a correction vector *only* considering drift rate. Thus, it only
    gives the 'current snapshot' of drift, and should eventually be combined
    with a running count of the correction vector to get a total correction.

    Note that this method can end up being negative if dt1 is after dt2.
    """
    if dt1 is None or dt2 is None:
        return NO_VEC

    return drift_rate * (dt2 - dt1).total_seconds()


def estimate_correction_no_snapshot(corr_info: CorrectionInfo | None,
                                    curr_dt: dt.datetime,
                                    ) -> CorrectionInfo | None:
    """Estimate CorrectionInfo when no snapshot was detected.

    We update the CorrectionInfo considering the drift rate and time of
    the prior CorrectionInfo.

    Args:
        corr_info: current CorrectionInfo. If None, we return None.
        curr_dt: datetime when the latest scan occurred (for which we could not
            find a match).

    Returns:
        np.ndarray of the updated correction vector (or None if no corr_info
        provided).
    """
    if corr_info is None:
        return None

    drift_vec = estimate_correction_vec(corr_info.drift_rate,
                                        corr_info.curr_dt, curr_dt)
    return CorrectionInfo(curr_dt, drift_vec, corr_info.drift_rate,
                          corr_info.unit)


def estimate_correction_from_snapshot(drift_snapshot: DriftSnapshot,
                                      corr_info: CorrectionInfo | None
                                      ) -> CorrectionInfo:
    """Estimate CorrectionInfo from a provided DriftSnapshot.

    Given a DriftSnapshot, we estimate the parameters for CorrectionInfo. This
    process is somewhat complicated:

    1. We estimate the drift rate based on the correction vector and the
    two timestamps. This is the detected drift rate due to this snapshot.
    2. Then, we need to account for any time overlap between our snapshot and
    the prior CorrectionInfo. It is possible that our snapshot's first scan
    predates the time of our last CorrectionInfo. We need to subtract the
    proportion of the vector associated with this time overlap. We accomplish
    that by using the snapshot drift rate to estimate the overlap vector (and
    then subtracting it).
    3. This updated snapshot now represents the proper 'delta' vector on top
    of the correction that has already been done. The true vector
    is the snapshot vector + the 'assumed' vector. The assumed vector is the
    correction vector we had already applied based on our prior
    CorrectionInfo's drift rate and the time between the last scan and this
    scan.
    4. The actual drift rate is then calculated considering the 'actual'
    vector and the dime delta.

    Args:
        drift_snapshot: DriftSnapshot we are suing to create a CorrectionInfo.
        corr_info: the latest CorrectionInfo we have, which might have a time
            overlap with our snapshot.

    Returns:
        CorrectionInfo.
    """
    snapshot_vec = drift_snapshot.vec
    snapshot_drift_rate = get_drift_rate(snapshot_vec,
                                         drift_snapshot.dt1,
                                         drift_snapshot.dt2)

    if corr_info is None:  # No fancy correction needed, snapshot says all
        return CorrectionInfo(drift_snapshot.dt2, snapshot_vec,
                              snapshot_drift_rate, drift_snapshot.unit)

    # Account for temporal overlap (if applicable)
    if corr_info.curr_dt is not None and drift_snapshot.dt2 is not None:
        overlap_time_delta_s = (corr_info.curr_dt - drift_snapshot.dt2
                                ).total_seconds()
        if overlap_time_delta_s > 0:  # There was overlap
            overlap_vec = snapshot_drift_rate * overlap_time_delta_s
            snapshot_vec -= overlap_vec

    # To calculate the proper correction info, we need to estimate the
    # 'actual' vector, by adding the snapshot vector to the assumed vector,
    # i.e. the translation we already did due to our assumed drift.
    assumed_vec = estimate_correction_vec(corr_info.drift_rate,
                                          corr_info.curr_dt,
                                          drift_snapshot.dt2)
    actual_vec = assumed_vec + snapshot_vec
    actual_rate = get_drift_rate(actual_vec, corr_info.curr_dt,
                                 drift_snapshot.dt2)
    return CorrectionInfo(drift_snapshot.dt2, actual_vec, actual_rate,
                          drift_snapshot.unit)


def update_total_correction(total_corr_info: CorrectionInfo | None,
                            latest_corr_info: CorrectionInfo | None,
                            update_weight: float,
                            ) -> CorrectionInfo | None:
    """Update the total correction based on a new estimate.

    Our created CorrectionInfos are based on DriftSnapshots, which are
    snapshots between two scans. Thus, they do not contain the *total* drift
    since the experiment began. For that, we need to continually add a kept
    total_corr_info's correction vector to it.

    For the updating, we also consider a 'weight' applied to the new data vs.
    the old data. The operation is essentially:
        updated_val = (1 - weight) * old_val + weight * new_val
    and is applied both to the drift rate and the update vector. A value of 1.0
    would mean we completely disregard prior drift rates in our calculations. A
    value of 0.5 would mean we average them with equal weights.

    For the vector, our update val considers the prior 'assumed' vector
    (considering the time delta and old drift rate) and the latest vector.
    The weighted result is added to the current 'total' vector (which contains
    the total drift vector over the experiment).

    Args:
        total_corr_info: total CorrectionInfo from the beginning of the
            experiment. If None, we only consider latest_corr_info.
        latest_corr_info: 'local' CorrectionInfo created from the latest
            estimation. If None, we return None (latest_corr_info being None
            implies that total_corr_info must be None, as the former was
            calculated from the latter).
        update_weight: how much to weight the new drift rate and vector vs.
            the prior one.

    Returns:
        CorrectionInfo where latest_corr_info's vec has been updated to
            contain that of total_corr_info (additive).
    """
    if latest_corr_info is None:  # This implies total_corr_info is None
        return None
    if total_corr_info is None:  # First time setting corr_info
        return latest_corr_info

    assumed_vec = estimate_correction_vec(total_corr_info.drift_rate,
                                          total_corr_info.curr_dt,
                                          latest_corr_info.curr_dt)
    update_vec = ((1 - update_weight) * assumed_vec +
                  update_weight * latest_corr_info.vec)
    vec = total_corr_info.vec + update_vec

    drift_rate = ((1 - update_weight) * total_corr_info.drift_rate +
                  update_weight * latest_corr_info.drift_rate)

    return CorrectionInfo(latest_corr_info.curr_dt,
                          vec, drift_rate, latest_corr_info.unit)


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
                              ) -> (xr.DataArray, xr.DataArray, float):
    """Extract intersection patches from images, matching spatial res.

    Extract patches from da1 and da2 corresponding to rect, and update
    them so they have matching spatial resolutions. We choose the higher
    resolution of the two patches as the resolution to scale to. For
    use later, we also return the resolution ratio between patch2 and
    the chosen one (so we can counter it later and ensure our data is
    in the proper scaling).

    Args:
        da1: First DataArray.
        da2: Second DataArray.
        rect: intersection rectangle.

    Returns:
        Tuple of associated patches (patch_da1, patch_da2, scale),
        where scale is the resolution ratio between patch2 and the
        chosen resolution.
    """
    patches = [extract_patch(da1, rect), extract_patch(da2, rect)]
    high_res_patch = (patches[0] if patches[0].shape > patches[1].shape
                      else patches[1])
    scale_da2 = np.array(high_res_patch.shape) / np.array(patches[1].shape)

    patches = [patch.interp_like(high_res_patch) for patch in patches]
    return tuple(patches) + (scale_da2,)


def scale_da(da: xr.DataArray, scale: float) -> xr.DataArray:
    """Scale DataArray resolution by scale (both dimensions)."""
    x2 = np.linspace(da.x[0], da.x[-1], int(len(da.x) * scale))
    y2 = np.linspace(da.y[0], da.y[-1], int(len(da.y) * scale))
    return da.interp(x=x2, y=y2)
