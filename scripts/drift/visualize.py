"""Script to visualize drift tracked during an experiment."""

import os
from typing import Any
import logging
import fire
import datetime as dt
from dataclasses import dataclass
import csv

import numpy as np

from afspm.utils import units
from afspm.utils import log
from afspm.utils import csv as utils_csv

from afspm.components.drift import scheduler

import matplotlib as mpl
from matplotlib import pyplot as plt


logger = logging.getLogger(__name__)


@dataclass
class DriftData:
    """Struct of different drift aspects."""

    scan_time_hours: np.ndarray
    drift_offsets: np.ndarray
    drift_rates: np.ndarray


TIMESTAMP_KEY = 'timestamp'
OFFSETS_KEY = 'pcs_to_scs_trans'
UNIT_KEY = 'pcs_to_scs_units'
RATES_KEY = 'pcs_to_scs_drift_rate'
EMPTY_STR = ''


def load_drift_data(csv_filepath: str, desired_offset_unit: str,
                    desired_rate_unit: str, time_as_seconds: bool = True
                    ) -> DriftData:
    """Given a CSV file of drift data, get a DriftData object."""
    csv_attribs = utils_csv.CSVAttributes(csv_filepath)
    with open(csv_filepath, 'r', newline='') as csv_file:
        kwargs = utils_csv.create_dict_kwargs(
            csv_file, csv_attribs,
            scheduler.CSCorrectedScheduler.CSV_FIELDS)
        reader = csv.DictReader(**kwargs)

        next(reader)  # Skip header

        zero_hrs = None
        scan_time_hours = []
        drift_offsets = []
        drift_rates = []
        for row in reader:
            if EMPTY_STR in row.values():
                continue  # Skip this row, missing data.

            offset_unit = (row[UNIT_KEY])
            rate_unit = offset_unit + '/s'

            hrs, zero_hrs = extract_scan_time_hours(float(row[TIMESTAMP_KEY]),
                                                    zero_hrs,
                                                    time_as_seconds)
            scan_time_hours.append(hrs)

            drift_offsets.append(units.convert_np(
                str_to_np(row[OFFSETS_KEY]), offset_unit, desired_offset_unit))
            drift_rates.append(units.convert_np(
                str_to_np(row[RATES_KEY]), rate_unit, desired_rate_unit))

        # Convert all to np arrays
        scan_time_hours = np.array(scan_time_hours)
        drift_offsets = np.array(drift_offsets)
        drift_rates = np.array(drift_rates)

        return DriftData(scan_time_hours, drift_offsets, drift_rates)


def str_to_np(val: str) -> np.ndarray:
    """Convert from our saved CSV format to np array.

    First and last elem are [], not read by fromstring. We also need to
    specify that the separator used is a space.
    """
    return np.fromstring(val[1:-1], sep=' ')


def extract_scan_time_hours(val: float | str, zero_hrs: float | None,
                            time_as_seconds: bool) -> (float, float):
    """Handle extracting scan time as hours and updating our zero_hrs."""
    if time_as_seconds:
        hrs = convert_seconds_to_hours(val)
    else:
        hrs = convert_isoformat_to_hours(val)

    if not zero_hrs:
        zero_hrs = hrs

    rel_hrs = hrs - zero_hrs
    return rel_hrs, zero_hrs


def convert_seconds_to_hours(ts: float) -> float:
    """Given a timestamp in seconds, convert to hours."""
    return ts / 3600


def convert_isoformat_to_hours(iso: str) -> float:
    """Given a tiemstamp in isoformat, convert to hours."""
    ts = dt.datetime.fromisoformat(iso).total_seconds()
    return ts / 3600


MARGIN_SCALING = 0.1
DEFAULT_SAVE_FILENAME = 'drift_correction.png'
DEFAULT_OFFSET_UNIT = 'nm'
DEFAULT_RATE_UNIT = 'nm/h'
SPATIAL_X_NAME = 'X'
SPATIAL_Y_NAME = 'Y'
TIME_NAME = 'Scan Time'
TIME_UNIT = 'hrs'
DRIFT_RATE_TITLE = 'Drift Rate'
DRIFT_OFFSET_TITLE = 'Drift Offset'


def draw_drift_rates(drift_data: DriftData, unit: str,
                     ax: plt.Axes, colors: Any):  # TODO: what is colors?
    """Draw drift rates on axis."""
    zeros = np.zeros(drift_data.drift_rates.shape[0])

    ax.quiver(zeros, zeros,
              drift_data.drift_rates[:, 0],
              drift_data.drift_rates[:, 1],
              angles='xy', scale_units='xy', scale=1,
              color=colors)

    ax.set_xlabel(f'{SPATIAL_X_NAME} [{unit}]')
    ax.set_ylabel(f'{SPATIAL_Y_NAME} [{unit}]')
    ax.set_title(DRIFT_RATE_TITLE)

    # Quiver is weird, so we need to explicit xlimit and ylimits?
    min_max_x = [np.min(drift_data.drift_rates[:, 0]),
                 np.max(drift_data.drift_rates[:, 0])]
    min_max_x = [min(min_max_x), max(min_max_x)]
    min_max_x = [min(0., min_max_x[0]) - MARGIN_SCALING * abs(min_max_x[0]),
                 max(0., min_max_x[1]) + MARGIN_SCALING * abs(min_max_x[1])]
    ax.set_xlim(min_max_x[0], min_max_x[1])
    min_max_y = [np.min(drift_data.drift_rates[:, 1]),
                 np.max(drift_data.drift_rates[:, 1])]
    min_max_y = [min(min_max_y), max(min_max_y)]
    min_max_y = [min(0., min_max_y[0]) - MARGIN_SCALING * abs(min_max_y[0]),
                 max(0., min_max_y[1]) + MARGIN_SCALING * abs(min_max_y[1])]
    ax.set_ylim(min_max_y[0], min_max_y[1])


def draw_drift_offsets(drift_data: DriftData, unit: str,
                       ax: plt.Axes, colors: Any):  # TODO: What is colors?
    """Draw drift offsets on axis."""
    ax.scatter(drift_data.drift_offsets[:, 0],
               drift_data.drift_offsets[:, 1],
               c=colors)
    ax.plot(drift_data.drift_offsets[:, 0],
            drift_data.drift_offsets[:, 1],
            color='lightgrey', linestyle='dashed')

    ax.set_xlabel(f'{SPATIAL_X_NAME} [{unit}]')
    ax.set_ylabel(f'{SPATIAL_Y_NAME} [{unit}]')
    ax.set_title(DRIFT_OFFSET_TITLE)

    ax.autoscale()


def get_colors_colorbar_for_time(drift_data, cm: str):
    """Create colors and colorbar for time range.

    I don't fully understand this logic, but it works. I got it from:
    https://stackoverflow.com/a/44285957
    """
    norm = mpl.colors.Normalize()
    norm.autoscale(drift_data.scan_time_hours)

    cm = mpl.colormaps[cm].resampled(drift_data.scan_time_hours.shape[0])
    colorbar = mpl.cm.ScalarMappable(cmap=cm, norm=norm)
    colors = cm(norm(drift_data.scan_time_hours))

    return colors, colorbar


def draw_data_axis(x_data: np.ndarray, y_data: np.ndarray,
                   x_meaning: str, y_meaning: str,
                   x_unit: str, y_unit: str, ax: plt.Axes, colors: Any):
    """Draw data of a particular axis."""
    ax.scatter(x_data, y_data, c=colors)
    ax.plot(x_data, y_data, color='lightgrey', linestyle='dashed')
    ax.set_xlabel(f'{x_meaning} [{x_unit}]')
    ax.set_ylabel(f'{y_meaning} [{y_unit}]')
    ax.autoscale


def draw_drift_data(csv_file: str,
                    desired_offset_unit: str = DEFAULT_OFFSET_UNIT,
                    desired_rate_unit: str = DEFAULT_RATE_UNIT,
                    time_as_seconds: bool = True,
                    display: bool = True,
                    save_file: str = DEFAULT_SAVE_FILENAME,
                    cm: str = 'nipy_spectral'):
    """Read a drift CSV file and visualize drift rate and offset.

    This method reads a CSV file created by CSCorrectedScheduler, and
    plots the drift offset and rates over time so they may be analyzed.

    The data is displayed (in a blocking fashion) if display is True, and
    saved to filename save_file in the same directory as the CSV file.

    Desired offset units and rate units must be specified. We use these to
    scale as needed.

    Args:
        csv_file: path to the csv file we wish to read.
        desired_offset_unit: desired offset unit. Defaults to 'nm'.
        desired_rate_unit: desired rate unit. Defaults to 'nm/h'.
        time_as_seconds: whether the timestamp in the csv file saved a
            timestamp in seconds, or an iso format str. Default is True,
            which indicates timestamp in seconds.
        display: whether or not we show the figure in a blocking fashion.
            Default is True.
        save_file: filename to save the drawn plot. This is the filename
            *without* the path, as we use the csv_files path. Defaults to
            'drift_correction.png'.
        cm: colormap style for visualization. Defaults to 'nipy_spectral'.
    """
    drift_data = load_drift_data(csv_file, desired_offset_unit,
                                 desired_rate_unit, time_as_seconds)

    fig = plt.figure(layout='constrained')
    mosaic = """AB
                AB
                AB
                CD
                EF"""
    axd = fig.subplot_mosaic(mosaic)

    # First, draw 'birds eye view' plots
    colors, colorbar = get_colors_colorbar_for_time(drift_data, cm)
    draw_drift_offsets(drift_data, desired_offset_unit, axd['A'], colors)
    draw_drift_rates(drift_data, desired_rate_unit, axd['B'], colors)

    fig.colorbar(colorbar, ax=list(axd.values()), shrink=0.6)

    draw_data_axis(drift_data.scan_time_hours, drift_data.drift_offsets[:, 0],
                   TIME_NAME, SPATIAL_X_NAME, TIME_UNIT,
                   desired_offset_unit, axd['C'], colors)
    draw_data_axis(drift_data.scan_time_hours, drift_data.drift_offsets[:, 1],
                   TIME_NAME, SPATIAL_Y_NAME, TIME_UNIT,
                   desired_offset_unit, axd['E'], colors)
    draw_data_axis(drift_data.scan_time_hours, drift_data.drift_rates[:, 0],
                   TIME_NAME, SPATIAL_X_NAME, TIME_UNIT,
                   desired_rate_unit, axd['D'], colors)
    draw_data_axis(drift_data.scan_time_hours, drift_data.drift_rates[:, 1],
                   TIME_NAME, SPATIAL_Y_NAME, TIME_UNIT,
                   desired_rate_unit, axd['F'], colors)

    if display:
        plt.show(block=True)

    save_path = os.path.join(os.path.dirname(csv_file), save_file)
    fig.savefig(save_path)


if __name__ == '__main__':
    log.set_up_logging(logger_name=None)  # Log everything!
    fire.Fire(draw_drift_data)
