""" ROI Analysis methods."""

import logging
from dataclasses import dataclass
import numpy as np

from google.protobuf.message import Message

from afspm.spawn import LOGGER_ROOT
from afspm.components.component import AfspmComponent

from afspm.io.protos.generated import scan_pb2
from afspm.io.protos.generated import geometry_pb2
from afspm.io.protos.generated import analysis_pb2


logger = logging.getLogger(LOGGER_ROOT + '.samples.point_subscan.' + __name__)


# ----- ROI Analysis Methods ----- #
@dataclass
class ROIAnalysisData:
    fscan_phys_size: list[float]
    num_points_to_output: int
    rng: np.random.Generator = np.random.default_rng()


def analyze_full_scan(component: AfspmComponent, envelope: str,
                      proto: Message, analysis_data: ROIAnalysisData):
    """For each 'full scan', output a random set of points of interest."""
    if isinstance(proto, scan_pb2.Scan2d):
        logger.debug("Scan received, analyzing...")
        logger.debug("Randomizing %s points...",
                     analysis_data.num_points_to_output)
        rand_vals = analysis_data.rng.random(
            (analysis_data.num_points_to_output, 2))
        rand_vals *= (analysis_data.fscan_phys_size[0],
                      analysis_data.fscan_phys_size[1])

        points_list = analysis_pb2.SpatialPointWithScoreList()
        for point in rand_vals:
            pt2d = geometry_pb2.Point2d(x=point[0], y=point[1])
            spatial = analysis_pb2.SpatialPoint(point=pt2d)
            pt_with_score = analysis_pb2.SpatialPointWithScore(
                spatial=spatial)
            points_list.spatials.append(pt_with_score)

        if component.publisher:
            logger.info("Publishing points: %s", points_list)
            component.publisher.send_msg(points_list)
