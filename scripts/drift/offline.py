"""Script to get measure drift offline on a dir of scans."""

import os
import logging
import fire
from collections import deque

from afspm.components.drift import drift
from afspm.components.drift import scheduler
import afspm.components.microscope.translators.asylum.translator as asylum

from afspm.utils import csv
from afspm.utils import log

from afspm.io.protos.generated import scan_pb2


logger = logging.getLogger(log.LOGGER_ROOT + '.scripts.drift.' + __name__)


DEFAULT_CSV = 'drift_snapshots.csv'
ASYLUM_EXT = '.ibw'


# Mapping from extension to file loader.
MAP_EXT_FILE_LOADER = {ASYLUM_EXT: asylum.load_scans_from_file}


class OfflineCSScheduler(scheduler.CSCorrectedScheduler):
    """Run drift estimation like the scheduler, offline.

    The biggest difference here is that our 'feedback loop' (of updating
    the PCS-to-SCS mapping) does not take place! Thus, even though we
    estimate a new origin each scan, it is *not* used to update the
    position of the next scan. So in principle we are only measuring
    drift snapshots between scans.

    We add a parameter for our 'faking':
       cache_size: indicate the size of the cache for scans. Note that this
            is the simplest cache, i.e. we are not caching based on regions
            or anything like this.
    """

    def __init__(self, channel_id: str, cache_size: int,
                 drift_model: drift.DriftModel | None = None,
                 csv_attribs: csv.CSVAttributes =
                 scheduler.CSCorrectedScheduler.DEFAULT_CSV_ATTRIBUTES,
                 min_intersection_ratio: float =
                 scheduler.CSCorrectedScheduler.DEFAULT_MIN_INTERSECTION_RATIO,
                 min_spatial_res_ratio: float =
                 scheduler.CSCorrectedScheduler.DEFAULT_MIN_SPATIAL_RES_RATIO,
                 max_fitting_score: float =
                 scheduler.CSCorrectedScheduler.DEFAULT_MAX_FITTING_SCORE,
                 update_weight: float =
                 scheduler.DEFAULT_UPDATE_WEIGHT,
                 rescan_intersection_ratio: float =
                 scheduler.CSCorrectedScheduler.DEFAULT_RESCAN_INTERSECTION_RATIO,
                 grab_oldest_match: bool = True,
                 **kwargs):
        """Initialize our correction scheduler."""
        self.channel_id = channel_id.upper()
        self.drift_model = (drift.create_drift_model() if drift_model is None
                            else drift_model)
        self.csv_attribs = csv_attribs
        self.min_intersection_ratio = min_intersection_ratio
        self.min_spatial_res_ratio = min_spatial_res_ratio
        self.max_fitting_score = max_fitting_score
        self.update_weight = update_weight
        self.rescan_intersection_ratio = rescan_intersection_ratio
        self.rerun_scan = False
        self.rerun_scan_params = None

        self.total_corr_info = None
        self.grab_oldest_match = grab_oldest_match
        self.figure = None
        self.display_fit = False
        self.publisher = None

        # Warn user if using default fitting score and not RANSAC fitting
        if (self.max_fitting_score == self.DEFAULT_MAX_FITTING_SCORE and
                self.drift_model.fitting != drift.FittingMethod.RANSAC):
            logger.warning('Using default fitting score for fitting method '
                           f'{drift_model.fitting} (i.e. not RANSAC). This '
                           'is probably too low!')

        csv.init_csv_file(self.csv_attribs, self.CSV_FIELDS_V2)

        self.fake_cache = deque(maxlen=cache_size)

    def _get_scans_from_cache(self) -> list[scan_pb2.Scan2d]:
        """Override, read from file path."""
        return self.fake_cache

    def update_cache(self, scan: scan_pb2.Scan2d):
        """Update our fake cache, so we can estimate drift."""
        self.fake_cache.append(scan)

    def update(self, new_scan: scan_pb2.Scan2d):
        """Override to cancel correction between scans.

        Or at least, this is the default behaviour, since we are offline...
        """
        super().update(new_scan)
        # Reset offset every time, since we cannot really fix this...
        self.total_corr_info = None

    def _update_io(self):
        pass

    def _update_ui(self):
        pass


def run_drift_estimation_on_dir(scan_dir: str, scan_ext: str,
                                cache_size: int, channel_id: str,
                                csv_filename: str = DEFAULT_CSV):
    """Run drift estimation on scans in directory, outputting results to csv.

    This method will grab all scans in scan_dir with extension scan_ext, and
    proceed to estimate drifts, outputting the results to csv_filename.

    Args:
        scan_dir: path of scan directory (as str).
        scan_ext: scan extension (as str).
        channel_id: name of scan channel we wish to use to analyze drift.
        cache_size: size of prior scans cache, used to determine the first
            scan we compare it to.
        csv_filename: desired output filename of CSV file.
    """
    if scan_ext not in MAP_EXT_FILE_LOADER:
        logger.error(f'No file loader in MAP_EXT_FILE_LOADER for {scan_ext}.')
        return

    load_scans = MAP_EXT_FILE_LOADER[scan_ext]
    channel_id = channel_id.upper()

    csv_attribs = csv.CSVAttributes(os.path.join(scan_dir, csv_filename))
    scheduler = OfflineCSScheduler(channel_id, cache_size,
                                   csv_attribs=csv_attribs)

    filenames = [f for f in sorted(os.listdir(scan_dir))
                 if f.endswith(scan_ext)]

    for fn in filenames:
        scans = load_scans(os.path.join(scan_dir, fn))
        desired_scan = [scan for scan in scans
                        if channel_id in scan.channel.upper()][0]

        scheduler.update(desired_scan)
        scheduler.update_cache(desired_scan)


def cli_run_drift_estimation_on_dir(scan_dir: str, scan_ext: str,
                                    cache_size: int, channel_id: str,
                                    csv_filename: str = DEFAULT_CSV,
                                    log_level: str = logging.INFO):
    """Run drift estimation on scans in directory, outputting results to csv.

    This method will grab all scans in scan_dir with extension scan_ext, and
    proceed to estimate drifts, outputting the results to csv_filename.

    Args:
        scan_dir: path of scan directory (as str).
        scan_ext: scan extension (as str).
        channel_id: name of scan channel we wish to use to analyze drift.
        cache_size: size of prior scans cache, used to determine the first
            scan we compare it to.
        csv_filename: desired output filename of CSV file.
        log_level: level to use for logging. Defaults to INFO.
    """
    log.set_up_logging(log_level=log_level)
    run_drift_estimation_on_dir(scan_dir, scan_ext, cache_size, channel_id,
                                csv_filename)


if __name__ == '__main__':
    fire.Fire(cli_run_drift_estimation_on_dir)
