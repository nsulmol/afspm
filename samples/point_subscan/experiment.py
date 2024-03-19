"""Experiment methods."""
import logging
import numpy as np
from dataclasses import dataclass

from afspm.utils.log import LOGGER_ROOT
from afspm.components.component import AfspmComponent

from afspm.io import common
from afspm.io.protos.generated import scan_pb2


logger = logging.getLogger(LOGGER_ROOT + '.samples.point_subscan.' + __name__)


# ----- Experiment methods ----- #
@dataclass
class ExperimentData:
    phys_units: str
    full_scan_res: list[int]
    full_scan_phys_origin: list[float]
    full_scan_phys_size: list[float]

    data_units: str
    sub_scan_res: list[int]
    sub_scan_phys_size: list[float]

    sub_scans_per_full_scan: int
    points_id: str  # Envelope for detected scan points

    scans_since_last_full_scan: int = 0

    def __post_init__(self):
        # Start with full scan
        self.scans_since_last_full_scan = self.sub_scans_per_full_scan


def get_next_scan_params(component: AfspmComponent,
                         exp_data: ExperimentData
                         ) -> (scan_pb2.ScanParameters2d | None):
    """This is the 'choose the next scan' method for ScanHandler.

    For ScanHandler, we determine if we are doing a full or sub-scan, and
    create a ScanParameters2d accoridngly. This is what we return (and
    thus, what ScanHandler receives).

    Returns:
        ScanParameters2d of the next scan, None if not yet determined.
    """
    if exp_data.scans_since_last_full_scan >= exp_data.sub_scans_per_full_scan:
        origin = exp_data.full_scan_phys_origin
        size = exp_data.full_scan_phys_size
        res = exp_data.full_scan_res
        logger.info("Performing full scan.")

        # Reset scan counter and subscan rng
        exp_data.scans_since_last_full_scan = 0
    else:
        # Decide on next point based on results from analyzer
        # (grabbing latest list of points)
        if exp_data.points_id not in component.subscriber.cache:
            logger.error("Could not find analysis points in cache! "
                         "Cannot continue.")
            return None

        # TODO: Should we be grabbing from 0 or -1???
        pts = component.subscriber.cache[exp_data.points_id][-1].spatials

        highest_pt = None
        for pt in pts:
            if not pt.HasField('score') or pt.score > highest_pt.score:
                highest_pt = pt

        if not highest_pt:  # Early return, no data yet!
            return None

        # NOTE: Assuming same units right now...
        point = [highest_pt.spatial.point.x,
                 highest_pt.spatial.point.y]

        origin, size = get_roi_within_bounds_fix_size(
            np.array(exp_data.full_scan_phys_origin),
            np.array(exp_data.full_scan_phys_size),
            np.array(point), np.array(exp_data.sub_scan_phys_size))

        origin = origin.tolist()
        size = size.tolist()
        res = exp_data.sub_scan_res

        logger.info(f"Performing subscan with origin, size: {origin}, {size}")
        exp_data.scans_since_last_full_scan += 1
    return common.create_scan_params_2d(origin, size,
                                        exp_data.phys_units,
                                        res, exp_data.data_units)


def get_roi_within_bounds_fix_size(bounds_origin: np.array,
                                   bounds_size: np.array,
                                   center_point: np.array,
                                   desired_size: np.array
                                   ) -> (np.array, np.array):
    """Given bounds, find an roi that fits but maintains the size.

    Returns:
        Tuple containing origin and size of desired roi.
    """
    assert (bounds_size > desired_size).all()  # If not, we have a problem!
    zeros = np.array([0, 0])

    lower_val = center_point - 0.5 * np.array(desired_size)
    upper_val = center_point + 0.5 * np.array(desired_size)
    bounds_max = bounds_origin + bounds_size

    lower_diff = np.maximum(bounds_origin - lower_val, zeros)
    upper_diff = np.maximum(upper_val - bounds_max, zeros)

    lower_val = lower_val - upper_diff + lower_diff
    return (lower_val, desired_size)
