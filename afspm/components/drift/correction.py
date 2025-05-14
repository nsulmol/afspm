"""Methods for estimating corrections between scans."""

from dataclasses import dataclass
import datetime as dt
import logging

import numpy as np
import xarray as xr
import matplotlib.pyplot as plt

from . import drift
from ...utils import array_converters as ac
from ...utils.units import convert_list
from ...utils import proto_geo

from ...io.protos.generated import scan_pb2
from ...io.protos.generated import geometry_pb2


logger = logging.getLogger(__name__)


DEFAULT_EMPTY_DATETIME = dt.datetime(1, 1, 1)

NO_VEC = np.array([0.0, 0.0])
DEFAULT_UNIT = 'nm'


@dataclass
class CorrectionInfo:
    """Holds information necessary to correct for drift."""

    curr_dt: dt.datetime = None
    vec: np.ndarray = NO_VEC
    drift_rate: np.ndarray = NO_VEC
    unit: str = DEFAULT_UNIT


@dataclass
class DriftSnapshot:
    """Holds info on the drift estimated between two scans."""

    dt1: dt.datetime = None  # Time of first scan.
    dt2: dt.datetime = None  # Time of second scan.
    # Translation vector to correct second to first scan CS.
    vec: np.ndarray = NO_VEC
    unit: str = DEFAULT_UNIT  # Translation vector unit


# TODO: Remove me?
# def time_intersection(a: DriftSnapshot, b: DriftSnapshot
#                       ) -> (dt.datetime, dt.datetime):
#     """Compute temporal intersection of two DriftSnapshots."""
#     dt1 = max(a.dt1, b.dt1)
#     dt2 = min(a.dt2, b.dt2)
#     if dt1 <= dt2:
#         return (dt1, dt2)
#     return (DEFAULT_EMPTY_DATETIME, DEFAULT_EMPTY_DATETIME)


# def time_intersection_delta(a: DriftSnapshot, b: DriftSnapshot
#                             ) -> dt.timedelta:
#     """Compute temporal intersection of two Correction infos, return delta."""
#     dt1, dt2 = time_intersection(a, b)
#     return dt2 - dt1


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

    # # TODO: swap with matching spatial resolutions!
    # spatial_resolutions = [proto_geo.spatial_resolution(scan)
    #                        for scan in [scan1, scan2]]
    # min_idx = spatial_resolutions.index(min(spatial_resolutions))
    # max_idx = min_idx + 1 % 2
    # required_scaling = spatial_resolutions[max_idx] / spatial_resolutions[min_idx]

    # new_das = []
    # for idx, da in enumerate([da1, da2]):
    #     new_das.append(da if idx == max_idx else scale_da(da, required_scaling))

    # transform, score = drift.estimate_transform(drift_model,
    #                                             new_das[0], new_das[1],
    #                                             display_fit,
    #                                             figure=figure)


    # required_scaling = proto_geo.spatial_resolution_ratio_b_a(scan1, scan2)
    # new_da1 = scale_da(da1, required_scaling)
    # transform, score = drift.estimate_transform(drift_model, new_da1, da2,
    #                                             display_fit,
    #                                             figure=figure)

    # Get intersection patches (scaled to scan2, as the transform is
    # for da2 to move to da1's position).
    inter_rect = proto_geo.rect_intersection(scan1.params.spatial.roi,
                                             scan2.params.spatial.roi)
    patch1, patch2 = extract_and_scale_patches(da1, da2, inter_rect)

    transform, score = drift.estimate_transform(drift_model, patch1, patch2,
                                                display_fit,
                                                figure=figure)

    # TODO: Having to check if transform is not None is ugly as all heck...
    # Why isn't the score just too low?
    # NOTE: We are doing -trans because we want to *correct* for the drift
    # detected. Maybe that should be elsewhere???  # TODO
    if score <= max_score and transform is not None:
        trans, units = drift.get_translation(da2, transform)
        drift_snapshot = DriftSnapshot(
            scan1.timestamp.ToDatetime(dt.timezone.utc),
            scan2.timestamp.ToDatetime(dt.timezone.utc),
            -trans,  # TODO: was np.array()
            units)  # also was trans!
        return drift_snapshot
    return None


def get_drift_rate(vec: np.ndarray, dt1: dt.datetime, dt2: dt.datetime
                   ) -> np.ndarray:
    if dt2 is None or dt1 is None:
        return NO_VEC

    try:
        return vec / (dt2 - dt1).total_seconds()
    except ZeroDivisionError:
        return NO_VEC


def get_average_drift_rate(drift_snapshots: list[DriftSnapshot]
                           ) -> np.ndarray:
    spatial_unit = drift_snapshots[-1].unit

    drift_rates = []
    for snapshot in drift_snapshots:
        units = (snapshot.unit, snapshot.unit)
        vec = convert_list(snapshot.vec, units,
                           (spatial_unit, spatial_unit))
        vec = np.array(vec)
        drift_rate = get_drift_rate(vec, snapshot.dt1, snapshot.dt2)
        drift_rates.append(drift_rate)
        logger.trace(f'vec: {vec}')
        logger.trace(f'drift rate: {drift_rate}')
        logger.trace(f'units: {units}')

    avg_drift_rate = np.mean(np.array(drift_rates), axis=0)
    return avg_drift_rate


def estimate_correction_vec(drift_rate: np.ndarray,
                            dt1: dt.datetime, dt2: dt.datetime) -> np.ndarray:
    """Estimate a correction vector given a drift rate and two DateTimes.

    This is a correction vector *only* considering drift rate. Thus, it only
    gives the 'current snapshot' of drift, and should eventually be combined
    with a running count of the correction vector to get a total correction.
    """
    if dt1 is None or dt2 is None:
        return NO_VEC
    if dt1 > dt2:  # Only add drift if our second timestamp is after our first.
        return NO_VEC

    return drift_rate * (dt2 - dt1).total_seconds()


def estimate_correction_from_history(drift_snapshots: list[DriftSnapshot],
                                     time: dt.datetime) -> CorrectionInfo:
    """Estimate a CorrectionInfo when no scan match was found.

    This method estimates a new CorrectionInfo from the prior history of
    DriftSnapshots. It is used for the cases where no scan matches were found.
    Thus, we use the prior DriftSnapshots to estimate an average drift rate,
    and use that drift rate to create a new CorrectionInfo.

    It calculates the drift rates (vec / time) of all of the
    DriftSnapshots in our history and averages them to get a drift rate
    estimate. Then, it computes the 'current snapshot' correction vector that
    we would expect given the drift rate and the time since the last scan.

    Args:
        drift_snapshots: the history of DriftSnapshots, to be used to
            estimate the drift rate and determine the time of the current
            correction vec.
        time: the time when the latest scan occurred (for which we could not
            find a match).

    Returns:
        np.ndarray of the updated correction vector.
    """
    dt1 = drift_snapshots[-1].dt2
    dt2 = time
    avg_drift_rate = get_average_drift_rate(drift_snapshots)
    vec = estimate_correction_vec(avg_drift_rate, dt1, dt2)
    return CorrectionInfo(dt2, vec, avg_drift_rate)


def correction_from_drift(drift_snapshot: DriftSnapshot) -> CorrectionInfo:
    """Estimate CorrectionInfo from a provided DriftSnapshot.

    Given a DriftSnapshot, we estimate the parameters for CorrectionInfo. The
    drift rate is estimated from the correction vector and the two timestamps.

    Args:
        drift_snapshot: DriftSnapshot we are suing to create a CorrectionInfo.

    Returns:
        CorrectionInfo.
    """
    drift_rate = get_drift_rate(drift_snapshot.vec, drift_snapshot.dt1,
                                drift_snapshot.dt2)
    return CorrectionInfo(drift_snapshot.dt2, drift_snapshot.vec, drift_rate,
                          drift_snapshot.unit)


def get_total_correction(total_corr_info: CorrectionInfo,
                         latest_corr_info: CorrectionInfo) -> CorrectionInfo:
    """Update the total correction based on a new estimate.

    Our created CorrectionInfos are based on DriftSnapshots, which are
    snapshots between two scans. Thus, they do not contain the *total* drift
    since the experiment began. For that, we need to continually add a kept
    total_corr_info's correction vector to it.

    Note that we *only* update the correction vector. The drift rate should be
    based on the latest_corr_info, as should the DateTime.

    Args:
        total_corr_info: total CorrectionInfo from the beginning of the
            experiment.
        latest_corr_info: 'local' CorrectionInfo created from the latest
            estimation.

    Returns:
        CorrectionInfo where latest_corr_info's vec has been updated to
            contain that of total_corr_info (additive).
    """
    latest_corr_info.vec += total_corr_info.vec
    return latest_corr_info


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


def scale_da(da: xr.DataArray, scale: float) -> xr.DataArray:
    """Scale DataArray resolution by scale (both dimensions)."""
    x2 = np.linspace(da.x[0], da.x[-1], int(len(da.x) * scale))
    y2 = np.linspace(da.y[0], da.y[-1], int(len(da.y) * scale))
    return da.interp(x=x2, y=y2)
