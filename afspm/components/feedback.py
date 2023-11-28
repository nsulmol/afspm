"""Feedback Analysis Methods and Components.

The logic used in this module is based on:

Kohl, Dominik, et al. "Auto-tuning PI controller for surface tracking in
atomic force microscopy-A practical approach." 2016 American Control Conference
(ACC). IEEE, 2016.
"""

import logging
import scipy
import numpy as np
import matplotlib.pyplot as plt
from dataclasses import dataclass

from google.protobuf.message import Message

from .component import AfspmComponent
from ..io import common

from ..io.protos.generated import geometry_pb2
from ..io.protos.generated import scan_pb2
from ..io.protos.generated import control_pb2
from ..io.protos.generated import feedback_pb2

from ..io.control.client import ControlClient

from ..utils import array_converters as ac


logger = logging.getLogger(__name__)


_SLOPE_FACTOR = np.sqrt(2)  # sqrt(2) == 2x power (|x|^2)
_OFFSET_FACTOR = 4
_THRESHOLD_FACTOR = 0.02  # 2%


# ---------- Feedback Analysis ---------- #
@dataclass
class FeedbackAnalysisConfig:
    over_slope_factor: float = _SLOPE_FACTOR  # k1 in paper
    over_offset_factor: float = _OFFSET_FACTOR  # k2 in paper
    under_slope_factor: float = 1 / _SLOPE_FACTOR  # k1 equiv. for under
    under_offset_factor: float = 1 / _OFFSET_FACTOR # k2 equiv. for under

    over_threshold: float = _THRESHOLD_FACTOR
    under_threshold: float = _THRESHOLD_FACTOR

    use_hanning_window: bool = True

    visualize_analysis: bool = False
    viz_block_plot: bool = False  # Whether or not to block plt.show()
    viz_plot_log: bool = True  # Whether or not to visualize log-plot
# TODO: Consider summing from multiple scans...


def analyze_feedback_on_scan(scan: scan_pb2.Scan2d,
                             config: FeedbackAnalysisConfig
                             ) -> feedback_pb2.FeedbackAnalysis:
    """Analyze a provided scan for feedback parameters optimality.

    See analyze_feedback_on_arr() for full explanation.

    Args:
        scan: the Scan to be analyzed
        config: the FeedbackAnalysis values to use for analyzing the data.

    Returns:
        A FeedbackAnalysis message, containing the over/under proportions.
    """

    # First, sum scanlines to get one total scanline
    xarr = ac.convert_scan_pb2_to_xarray(scan)
    over_proportion, under_proportion = analyze_feedback_on_arr(xarr, config)
    logger.debug("Feedback Analysis: Over Proportion = %s, "
                 "Under Proportion = %s", over_proportion, under_proportion)
    return feedback_pb2.FeedbackAnalysis(
        proportionOverThreshold=over_proportion,
        proportionUnderThreshold=under_proportion)


# Pink = 1/f
# def func_pink(x, a, b):
#     return (a / x) + b


# def feedback_on_arr2(arr: np.ndarray,
#                      config: FeedbackAnalysisConfig,
#                      ) -> (float, float):

#     x_f, y_f = convert_freq_domain(arr, config.use_hanning_window)

#     # Remove first value, often x=0, which breaks fitting
#     x_f = x_f[1:]
#     y_f = y_f[1:]

#     from scipy.optimize import curve_fit
#     fit, pcov = curve_fit(func_pink, x_f, y_f)
#     print(f'Fit: {fit}')
#     y_fit = func_pink(x_f, *fit)

#     over_fit = fit * [config.over_slope_factor, config.over_offset_factor]
#     over_y = func_pink(x_f, *over_fit)
#     over_y_bool = y_f > over_y

#     under_fit = fit * [config.under_slope_factor, config.under_offset_factor]
#     under_y = func_pink(x_f, *under_fit)
#     under_y_bool = y_f < under_y

#     if config.visualize_analysis:
#         visualize_analysis(x_f, y_f, y_fit, over_y, under_y,
#                            config.viz_block_plot, config.viz_plot_log)



def analyze_feedback_on_arr(arr: np.ndarray,
                            config: FeedbackAnalysisConfig
                            ) -> (float, float):
    """Analyze a provided numpy array for feedback parameters optimality.

    This method will, given an array, analyze the scan lines to see if it
    is 'optimal' according to an assumption of a 1/f distribution signal. It
    accomplishes this by analyzing the signal in the frequency domain; fitting
    its distribution to a 1/f form (a/f + b); and measuring the percentage of
    the signal which is *over* or *under* the fit line (+/- some padding
    factors).

    If a 'sufficient' proportion of the signal is over the line, we believe
    that the feedback parameters are too high and should be reigned in. If a
    sufficient proportion of the signal is below the line, we believe the
    feedback parameters could be optimized up. However, we do not threshold the
    information on output, but rather provide the proportion of the data over/
    under the threshold in an analysis message.

    Args:
        arr: numpy array (or xarray DataArray) to be analysed.
        config: the FeedbackAnalysis values to use for analyzing the data.

    Returns:
        A tuple containing the (over proportion, under proportion).
    """
    x_f, y_f = convert_freq_domain(arr, config.use_hanning_window)

    # Remove first value, often x=0, which breaks fitting
    x_f = x_f[1:]
    y_f = y_f[1:]

    x_inv_f = 1 / x_f
    fit = np.polynomial.polynomial.polyfit(x_inv_f, y_f, 1)

    over_under_y = []
    over_under_y_bool = []
    for offset_factor, slope_factor in zip(
            [config.over_offset_factor, config.under_offset_factor],
            [config.over_slope_factor, config.under_slope_factor]):
        offset_factor = (offset_factor if fit[0] > 0 else
                         1 / offset_factor)
        mod_fit = fit * [offset_factor, slope_factor]
        over_under_y.append(np.polynomial.polynomial.polyval(x_inv_f, mod_fit))
        over_under_y_bool.append(y_f > over_under_y[-1])

    over_under_y_bool[1] = 1 - over_under_y_bool[1]

    # offset_factor = (config.over_offset_factor if fit[0] > 0 else
    #                  1 / config.over_offset_factor)
    # over_fit = fit * [offset_factor, config.over_slope_factor]
    # over_y = np.polynomial.polynomial.polyval(x_inv_f, over_fit)
    # over_y_bool = y_f > over_y

    # offset_factor = (config.under_offset_factor if fit[0] > 0 else
    #                  1 / config.under_offset_factor)
    # under_fit = fit * [offset_factor, config.under_slope_factor]
    # under_y = np.polynomial.polynomial.polyval(x_inv_f, under_fit)
    # under_y_bool = y_f < under_y

    if config.visualize_analysis:
        y_fit = np.polynomial.polynomial.polyval(x_inv_f, fit)
        logger.debug("Polynomial fit to: %f / x + %f", fit[1], fit[0])
        visualize_analysis(x_f, y_f, y_fit, over_under_y[0], over_under_y[1],
                           config.viz_block_plot, config.viz_plot_log)

    return (np.count_nonzero(over_under_y_bool[0]) / over_under_y_bool[0].size,
            np.count_nonzero(over_under_y_bool[1]) / over_under_y_bool[1].size)


def convert_freq_domain(arr: np.ndarray,
                        use_hanning_window: bool) -> (np.ndarray, np.ndarray):
    """Converts data from spatial domain to frequency domain.

    Given a numpy array, converts to frequency domain by (a) passing
    a Hann window over the data, and (b) running the FFT on it.

    Note that we are returning a 1D FFT, averaging all rows if we have
    a 2D image (i.e. treating each row as a scanline and averaging FFT
    results).

    For the output data, we deal only with the positive component, and
    multiply by 2 to account for the negative component. We also remove
    the 1st component, as it is noisy and can cause issues if inverting.

    Args:
        arr: input numpy ndarray (or xarray DataArray) to convert.
        use_hanning_window: whether or not to smooth the input image
            via a hanning window.

    Returns:
        (x-vals, y-vals) of the data in the frequency domain.
    """
    if arr.ndim > 2:
        logger.error("Multi-channel image provided, grabbing first channel.")
        arr = arr[:, :, 0]  # Only grab 1 channel

    n = arr.shape[0] if arr.ndim != 1 else 1

    if use_hanning_window:
        # ----- Alternative: np.hanning(arr.shape[-1])
        hann = scipy.signal.windows.hann(arr.shape[-1], False)
        windowed_lines = hann * arr
    else:
        windowed_lines = arr

    y_f = np.fft.fft(windowed_lines) / arr.shape[0]  # Last axis used
    if n != 1:
        y_f = np.sum(y_f, 0) / n  # Average over all scan lines

    x_f = np.fft.fftfreq(arr.shape[-1], d=1)

    # Extract only positive, real portion
    pos_end = np.argmax(x_f < 0)

    x_f = x_f[0:pos_end]
    y_f = 2 * np.real(np.abs(y_f[0:pos_end]))
    return (x_f, y_f)


def visualize_analysis(x: np.ndarray,
                       y: np.ndarray,
                       fit_y: np.ndarray = None,
                       over_y: np.ndarray = None,
                       under_y: np.ndarray = None,
                       block_viz: bool = False,
                       plot_log: bool = True):
    """Visualize the resulting fit."""
    plt.plot(x, y, 'ob:', label='data')
    if fit_y is not None:
        plt.plot(x, fit_y, 'k-', label='fit 1/f')
    if over_y is not None:
        plt.plot(x, over_y, 'g--', label='over 1/f')
    if under_y is not None:
        plt.plot(x, under_y, 'r--', label='under 1/f')
    if plot_log:
        plt.yscale('log')
        plt.xscale('log')
    plt.legend()
    plt.show(block=block_viz)


class FeedbackAnalyzer(AfspmComponent):
    """Component that analyzes scans for Z-Control Feedback issues.

    FeedbackAnalyzer analyzes incoming scans for under- or over- set
    Z-Control Feedback parameters. If either is found, it reports the
    appropriate problem with the Device Controller. If a publisher is
    provided, it publishes the over- and under- setting proportions.
    """
    def __init__(self, config: FeedbackAnalysisConfig, **kwargs):
        self.config = config
        super().__init__(**kwargs)

    def on_message_received(self, envelope: str, proto: Message):
        """Override to analyze received scans."""
        if isinstance(proto, scan_pb2.Scan2d):
            res = analyze_feedback_on_scan(proto, self.config)

            if res.proportionOverThreshold > self.config.over_threshold:
                self.control_client.add_experiment_problem(
                    control_pb2.ExperimentProblem.EP_FEEDBACK_OVER_SET)
            elif res.proportionUnderThreshold > self.config.under_threshold:
                self.control_client.add_experiment_problem(
                    control_pb2.ExperimentProblem.EP_FEEDBACK_UNDER_SET)

            if self.publisher:
                self.publisher.send_msg(res)
