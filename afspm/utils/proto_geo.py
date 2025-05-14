"""Spatial comparison with our protobuf Messages."""


import logging
import numpy as np

from ..io.protos.generated import scan_pb2
from ..io.protos.generated import geometry_pb2


logger = logging.getLogger(__name__)


def rect_intersection(a: geometry_pb2.Rect2d, b: geometry_pb2.Rect2d
                      ) -> geometry_pb2.Rect2d:
    """Compute intersection of two Rect2ds."""
    # TODO: Why does this crash?
    # if a.HasField(ANGLE_FIELD) or b.HasField(ANGLE_FIELD):
    #     msg = ('Rect2d passed to rect_intersection with angle field. '
    #            'This is not currently supported.')
    #     logger.error(msg)
    #     raise ValueError(msg)

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
    """Compute the spatial resolution (res / spatial) of a scan.

    We grab the mean of the two spatial resolutions.
    """
    spatials = np.array([a.params.spatial.roi.size.x,
                         a.params.spatial.roi.size.y])
    resolutions = np.array([a.params.data.shape.x, a.params.data.shape.y])
    spatial_resolutions = resolutions / spatials
    return float(np.mean(spatial_resolutions))


def spatial_resolution_ratio_b_a(a: scan_pb2.Scan2d,
                                 b: scan_pb2.Scan2d) -> float:
    """Compute the ratio of the spatial resolutions of b / a."""
    spatial_resolutions = [spatial_resolution(a), spatial_resolution(b)]
    return min(spatial_resolutions) / max(spatial_resolutions)


def spatial_resolution_ratio_min_max(a: scan_pb2.Scan2d,
                                     b: scan_pb2.Scan2d) -> float:
    """Compute the ratio of the spatial resolutions of min / max.

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
    for idx, scan in enumerate(scans):
        inter_ratio = intersection_ratio(scan.params.spatial.roi,
                                         new_scan.params.spatial.roi)
        spatial_res_ratio = spatial_resolution_ratio_min_max(scan, new_scan)

        logger.trace(f'For scan {idx}, inter_ratio: {inter_ratio}, '
                     f'spatial_res_ratio: {spatial_res_ratio}')

        if (inter_ratio >= min_intersection_ratio and
                spatial_res_ratio >= min_spatial_res_ratio):
            intersect_scans.append(scan)

    logger.trace(f'Intersected scans length: {len(intersect_scans)}')

    if not intersect_scans:
        return None

    intersect_scans.sort(key=lambda scan: scan.timestamp.ToDatetime())
    return intersect_scans[-1]  # Last value is latest timestamp
