"""Experiment methods."""
import logging
import time
from dataclasses import dataclass

from afspm.utils.log import LOGGER_ROOT
from afspm.components.component import AfspmComponent

from afspm.io.protos.generated import scan_pb2
from afspm.io.common import create_scan_params_2d


logger = logging.getLogger(LOGGER_ROOT + '.samples.keep_scanning.' + __name__)


@dataclass
class ExperimentData:
    """The data we want to store between calls to get_next_scan_params."""

    scan_origin: tuple[float, float]
    scan_size: tuple[float, float]
    length_units: str
    angular_units: str = 'degrees'
    scan_res: tuple[int, int]
    scan_wait_s: float

    scan_sleep_ts: float = None


def get_next_scan_params(component: AfspmComponent,
                         exp_data: ExperimentData
                         ) -> (scan_pb2.ScanParameters2d | None):
    """Choose the next scan method for ScanHandler.

    In this case, we are just re-scanning the provided scan. The idea is that
    we keep scanning the same region over a period of time.

    Returns:
        ScanParameters2d of the next scan, None if not yet determined.
    """
    if exp_data.scan_sleep_ts is not None:
        ready_to_scan = (time.time() - exp_data.scan_sleep_ts >
                         exp_data.scan_wait_s)
        if not ready_to_scan:
            logger.debug('Insufficient time has passed between scans. '
                         'Returning None.')
            return None

    logger.debug('Sending out old scan params to keep scanning.')
    scan_params = create_scan_params_2d(exp_data.scan_origin,
                                        exp_data.scan_size,
                                        exp_data.length_units,
                                        exp_data.angular_units,
                                        exp_data.scan_res)
    exp_data.scan_sleep_ts = time.time()
    return scan_params
