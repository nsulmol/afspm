"""Experiment methods."""
import logging
from dataclasses import dataclass

from afspm.utils.log import LOGGER_ROOT
from afspm.components.component import AfspmComponent

from afspm.io.protos.generated import scan_pb2


logger = logging.getLogger(LOGGER_ROOT + '.samples.keep_scanning.' + __name__)


@dataclass
class ExperimentData:
    """The data we want to store between calls to get_next_scan_params."""

    scan_id: str  # Envelope for scan id.


def get_next_scan_params(component: AfspmComponent,
                         exp_data: ExperimentData
                         ) -> (scan_pb2.ScanParameters2d | None):
    """Choose the next scan method for ScanHandler.

    In this case, we are just re-scanning the last scan. The idea is that
    we keep scanning the same region over a period of time.

    Returns:
        ScanParameters2d of the next scan, None if not yet determined.
    """
    envelopes = [env for env in list(component.subscriber.cache.keys())
                 if exp_data.scan_id in env]

    if len(envelopes) == 0:
        logger.error(f'No protos matching provided scan_id {exp_data.scan_id} '
                     'received.')
        return None

    env = envelopes[0]  # Grab first envelope that matches our scan_id
    if len(component.subscriber.cache[env]) == 0:
        logger.error(f'No scan has been received with env {env}!')
        return None

    scan_params = component.subscriber.cache[env][-1].params
    return scan_params
