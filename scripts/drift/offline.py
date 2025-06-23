"""Script to get measure drift offline on a dir of scans."""

import os
import logging
import fire

from afspm.components.drift import drift
from afspm.components.drift import correction
from afspm.components.drift import scheduler
import afspm.components.microscope.translators.asylum.translator as asylum

from afspm.utils import csv

logger = logging.getLogger(__name__)


DEFAULT_CSV = './fake_drift_estimates.csv'
ASYLUM_EXT = '.ibw'


# Mapping from extension to file loader.
MAP_EXT_FILE_LOADER = {ASYLUM_EXT: asylum.load_scans_from_file}


def run_drift_estimation_on_dir(scan_dir: str, scan_ext: str,
                                max_fitting_score: float =
                                drift.DEFAULT_RESIDUAL_THRESH_PERCENT,
                                csv_filename: str = DEFAULT_CSV,
                                accumulate_vectors: bool = True):
    """Run drift estimation on scans in directory, outputting results to csv.

    This method will grab all scans in scan_dir with extension scan_ext, and
    proceed to estimate between-scan drifts, outputting the results to
    csv_filename. The user may choose whether the resulting drift
    vectors are cumulative or not via accumulate_vectors.

    NOTE: This method only calculates drift considering the immediately prior
    scan. This is less robust then the method done online, but a reasonable
    estimation method.

    Args:
        scan_dir: path of scan directory (as str).
        scan_ext: scan extension (as str).
        max_fitting_score: fitting score applied to drift estimation filtering.
        csv_filename: desired output filename of CSV file.
        accumulate_vectors: whether or not we accumulate the drift vectors
            over time.
    """
    if scan_ext not in MAP_EXT_FILE_LOADER:
        logger.error(f'No file loader in MAP_EXT_FILE_LOADER for {scan_ext}.')
        return

    load_scan = MAP_EXT_FILE_LOADER[scan_ext]
    drift_model = drift.create_drift_model()

    csv_attribs = csv.CSVAttributes(os.path.join(scan_dir, csv_filename))
    CSV_FIELDS = scheduler.CSCorrectedScheduler.CSV_FIELDS
    csv.init_csv_file(csv_attribs, CSV_FIELDS)

    total_corr_info = None
    filenames = [f for f in sorted(os.listdir(scan_dir))
                 if f.endswith(scan_ext)]
    for fn1, fn2 in zip(filenames[:-1], filenames[1:]):
        scan1 = load_scan(os.path.join(scan_dir, fn1))[0]
        scan2 = load_scan(os.path.join(scan_dir, fn2))[0]

        # Estimate drift
        snapshot = correction.compute_drift_snapshot(scan1, scan2, drift_model,
                                                     max_fitting_score)
        corr_info = correction.estimate_correction_from_snapshot(snapshot,
                                                                 None)

        if accumulate_vectors:
            total_corr_info = correction.update_total_correction(
                total_corr_info, corr_info, 1.0)
            corr_info = total_corr_info

        row_vals = scheduler.get_metadata_row(scan2, corr_info, True)
        csv.save_csv_row(csv_attribs, CSV_FIELDS, row_vals)


if __name__ == '__main__':
    fire.Fire(run_drift_estimation_on_dir)
