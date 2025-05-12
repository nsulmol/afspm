"""Contains methods for visualizing our experiment."""

import logging
from dataclasses import dataclass

from google.protobuf.message import Message

from afspm.utils.log import LOGGER_ROOT
from afspm.components.visualizer import Visualizer

from afspm.io.protos.generated import scan_pb2


logger = logging.getLogger(LOGGER_ROOT + '.samples.point_subscan.' + __name__)


# ----- Visualizer Methods ----- #
@dataclass
class VisualizerData:
    """Data needed in history for reset_scans."""

    num_scans_before_reset: int
    delete_sub_key: str
    scans_since_reset = 0


def reset_scans(component: Visualizer, envelope: str,
                proto: Message, viz_data: VisualizerData):
    """Override: we update our scan counter and reset if needed."""
    if isinstance(proto, scan_pb2.Scan2d):
        viz_data.scans_since_reset += 1

        if viz_data.scans_since_reset > viz_data.num_scans_before_reset:
            viz_data.scans_since_reset = 0

            # Reset all cache keys linked to our delete sub key
            for key in list(component.subscriber.cache.keys()):
                if viz_data.delete_sub_key in key:
                    del component.subscriber.cache[key]
