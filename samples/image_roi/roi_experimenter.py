"""Experimenter that alternates between large and small scans."""

import logging
from typing import Callable
import numpy as np

from google.protobuf.message import Message

from afspm.spawn import LOGGER_ROOT
from afspm.components.afspm.component import AfspmComponent
from afspm.components.helpers.scan_handler import ScanHandler
from afspm.io import common
from afspm.io.protos.generated import scan_pb2


logger = logging.getLogger(LOGGER_ROOT + '.samples.image_roi.' + __name__)


class ROIExperimenter(AfspmComponent):
    """An experimenter that alternates between large and small scans.

    More specifically, it will perform one 'large' scan (full scan range,
    lower resolution) every N scans, followed by (N-1) 'small' scans (sub-scan
    range, higher resolution), and repeat indefinitely.

    From an experimental perspective, it can be seen as scanning a larger
    region over time, while in-between focusing on smaller sub-regions that
    may be of interest.

    For the small scans, it divides the full scan range by sub_rois_per_dim,
    and randomly chooses one among that list every time. We ensure we do not
    grab the same ROI when performing sub-scans, but reset this every time
    a large scan is performed.

    However, this should still be considered a 'sample', as in a true
    experiment we would likely want to suggest ROIs based on a size and
    point in space, not by perfectly splitting up the grid.

    Attributes:
        phys_units: the units of the physical dimensions (i.e. x/y dimension),
            str.
        data_units: the units of the scan data (i.e. z-dimension), str.
        fscan_phys_origin: the physical origin of the full scan.
        fscan_phys_size: the physical size of the full scan.
        fscan_res: the scan resolution of the full scan.
        sscan_origins: the sub-scan origins (list).
        sscan_phys_size: the physical size of all sub-scans.
        sscan_res: the scan resolution of all sub-scans.
        sscans_per_fscan: how many sub-scans we perform between every full
            scan.

        scans_since_last_fscan: counter of current number of sub-scans since
            the last full scan.
        rerun_wait_s: how long to wait between reruns of a particular scan,
            if the scan fails for some reason mid-scan.

        get_envelope_for_proto: method that maps from proto message to
            our desired publisher 'envelope' string.
        get_envelope_kwargs: any additional arguments to be fed to
            get_envelope_for_proto.

        rng: random number generator, to choose the next sub-scan.

        scan_handler: ScanHandler, for performing scans.

        fscan_key: the cache key for full scan.
        sscan_key: the cache key for sub-scan.
    """
    def __init__(self, full_scan_res: list[int],
                 full_scan_physical_origin: list[float],
                 full_scan_physical_size: list[float],
                 physical_units: str, data_units: str,
                 sub_rois_per_dim: int,
                 sub_scan_res: list[int],
                 sub_scans_per_full_scan: int, rerun_wait_s: int,
                 get_envelope_for_proto: Callable[[Message], str],
                 get_envelope_kwargs: dict = None, **kwargs):
        """ Initialize ROIExperimenter.

        Args:
            full_scan_res: the scan resolution of the full scan.
            full_scan_physical_origin: the physical origin of the full scan.
            full_scan_physical_size: the physical size of the full scan.
            physical_units: the units of the physical dimensions (i.e. x/y
                dimension), str.
            data_units: the units of the scan data (i.e. z-dimension), str.
            sub_rois_per_dim: how many sub-ROIs to divide the full scan into,
                per dimension.
            sub_scan_res: the scan resolution of all sub-scans.
            sub_scans_per_full_scan: how many sub-scans we perform between
                every full scan.
            get_envelope_for_proto: method that maps from proto message to
                our desired publisher 'envelope' string.
            get_envelope_kwargs: any additional arguments to be fed to
                get_envelope_for_proto.
        """
        self.phys_units = physical_units
        self.data_units = data_units
        self.fscan_phys_origin = np.asarray(full_scan_physical_origin,
                                            np.float32)
        self.fscan_phys_size = np.asarray(full_scan_physical_size, np.float32)
        self.fscan_res = np.asarray(full_scan_res, np.uint)
        self.sscan_res = np.asarray(sub_scan_res, np.uint)
        self.sscans_per_fscan = sub_scans_per_full_scan
        self.get_envelope_for_proto = get_envelope_for_proto
        self.get_envelope_kwargs = (get_envelope_kwargs if get_envelope_kwargs
                                    else {})
        self.rerun_wait_s = rerun_wait_s

        self.scans_since_last_fscan = self.sscans_per_fscan

        self.rng = np.random.default_rng()  # For non-replacement random choice

        self.scan_handler = ScanHandler(self.rerun_wait_s,
                                        self.get_scan_params_for_next_scan)

        self.sscan_origins = None
        self.sscan_phys_size = None
        self._set_up_sub_scans(sub_rois_per_dim)

        self.fscan_key = None
        self.sscan_key = None

        super().__init__(**kwargs)

    def _get_key_for_size(self, size: np.ndarray) -> str:
        """Determines cache key for a particular Scan2d size."""
        scan_params = common.create_scan_params_2d(size=[size[0], size[1]])
        scan_2d = common.create_scan_2d(scan_params=scan_params)
        return self.get_envelope_for_proto(scan_2d, **self.get_envelope_kwargs)

    def _set_up_sub_scans(self, sub_rois_per_dim: int):
        """Initiailizes sscan_origins and sscan_phys_size."""
        x = np.linspace(self.fscan_phys_origin[0], self.fscan_phys_size[0],
                        sub_rois_per_dim, endpoint=False)
        y = np.linspace(self.fscan_phys_origin[1], self.fscan_phys_size[1],
                        sub_rois_per_dim, endpoint=False)
        x_points, y_points = np.meshgrid(x, y)
        self.sscan_origins = np.array([x_points.flatten(),
                                       y_points.flatten()]).T
        self.sscan_phys_size = np.array([x[1] - x[0], y[1] - y[0]])

    def on_message_received(self, envelope: str, proto: Message):
        """Override: we update the ScanHandler."""
        self.scan_handler.on_message_received(proto, self.control_client)

    def run_per_loop(self):
        """Override: we update the ScanHandler."""
        self.scan_handler.handle_resends(self.control_client)

    def get_scan_params_for_next_scan(self) -> scan_pb2.ScanParameters2d:
        """This is the 'choose the next scan' method for ScanHandler.

        For ScanHandler, we determine if we are doing a full or sub-scan, and
        create a ScanParameters2d accoridngly. This is what we return (and
        thus, what ScanHandler receives).

        Returns:
            ScanParameters2d of the next scan.
        """
        if self.scans_since_last_fscan >= self.sscans_per_fscan:
            origin = self.fscan_phys_origin
            size = self.fscan_phys_size
            res = self.fscan_res
            logger.info("Performing full scan.")

            # Reset scan counter and subscan rng
            self._reset_sub_scan_aspects()
        else:
            origin = self.rng.choice(self.sscan_origins, replace=False)
            size = self.sscan_phys_size
            res = self.sscan_res
            logger.info("Performing subscan with origin: %s",
                        origin)
            self.scans_since_last_fscan += 1

        return common.create_scan_params_2d(origin.tolist(), size.tolist(),
                                            self.phys_units, res.tolist(),
                                            self.data_units)

    def _reset_sub_scan_aspects(self):
        """After a full scan, reset our subscan aspects."""

        # Update counter to next full scan.
        self.scans_since_last_fscan = 0

        # Update rng to restart knowledge of what we scanned previously
        self.rng = np.random.default_rng()
