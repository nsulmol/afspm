"""Feedback Analysis Methods and Components.

The logic used in this module is based on:

Kohl, Dominik, et al. "Auto-tuning PI controller for surface tracking in
atomic force microscopy-A practical approach." 2016 American Control Conference
(ACC). IEEE, 2016.
"""

import logging
import copy
from enum import Enum, auto
import scipy
import numpy as np
import matplotlib.pyplot as plt
from dataclasses import dataclass

from google.protobuf.message import Message

from .component import AfspmComponent
from .scan.handler import ScanningComponent

from ..io.protos.generated import scan_pb2
from ..io.protos.generated import control_pb2
from ..io.protos.generated import feedback_pb2


from ..io.control.client import send_req_handle_ctrl
from ..utils import array_converters as ac


logger = logging.getLogger(__name__)


# ---------- Feedback Analysis ---------- #
_SLOPE_FACTOR = np.sqrt(2)  # sqrt(2) == 2x power (|x|^2)
_OFFSET_FACTOR = 4
_THRESHOLD_FACTOR = 0.02  # 2%


@dataclass
class AnalysisConfig:
    """Configuration parameters for feedback analysis."""

    over_slope_factor: float = _SLOPE_FACTOR  # k1 in paper
    over_offset_factor: float = _OFFSET_FACTOR  # k2 in paper
    under_slope_factor: float = 1 / _SLOPE_FACTOR  # k1 equiv. for under
    under_offset_factor: float = 1 / _OFFSET_FACTOR  # k2 equiv. for under

    over_threshold: float = _THRESHOLD_FACTOR
    under_threshold: float = _THRESHOLD_FACTOR

    use_hanning_window: bool = True

    visualize_analysis: bool = False
    viz_block_plot: bool = False  # Whether or not to block plt.show()
    viz_plot_log: bool = True  # Whether or not to visualize log-plot
# TODO: Consider summing from multiple scans...


def analyze_feedback_on_scan(scan: scan_pb2.Scan2d,
                             config: AnalysisConfig
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
    logger.debug(f"Feedback Analysis: Over Proportion = {over_proportion}, "
                 f"Under Proportion = {under_proportion}")
    return feedback_pb2.FeedbackAnalysis(
        proportionOverThreshold=over_proportion,
        proportionUnderThreshold=under_proportion)


def analyze_feedback_on_arr(arr: np.ndarray,
                            config: AnalysisConfig
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

    if config.visualize_analysis:
        y_fit = np.polynomial.polynomial.polyval(x_inv_f, fit)
        logger.debug(f"Polynomial fit to: {fit[1]} / x + {fit[0]}")
        visualize_analysis(x_f, y_f, y_fit, over_under_y[0], over_under_y[1],
                           config.viz_block_plot, config.viz_plot_log)

    return (np.count_nonzero(over_under_y_bool[0]) / over_under_y_bool[0].size,
            np.count_nonzero(over_under_y_bool[1]) / over_under_y_bool[1].size)


def convert_freq_domain(arr: np.ndarray,
                        use_hanning_window: bool) -> (np.ndarray, np.ndarray):
    """Convert data from spatial domain to frequency domain.

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
    appropriate problem with the microscope translator. If a publisher is
    provided, it publishes the over- and under- setting proportions.

    Attributes:
        config: AnalysisConfig, defining how the analysis should run.
        _zctrl_params: store ZCtrlParams, as we only want to run our
            analysis if the feedback is actually on.
    """

    def __init__(self, config: AnalysisConfig, **kwargs):
        self.config = config
        self._zctrl_params = None
        super().__init__(**kwargs)

    def on_message_received(self, envelope: str, proto: Message):
        """Override to analyze received scans."""
        if isinstance(proto, feedback_pb2.ZCtrlParameters):
            self._zctrl_params = proto
        elif isinstance(proto, scan_pb2.Scan2d):
            is_feedback_on = (self._zctrl_params and
                              self._zctrl_params.feedbackOn)
            if not is_feedback_on:
                return

            res = analyze_feedback_on_scan(proto, self.config)

            # TODO: Consider proportion under threshold as well?
            if (res.proportionOverThreshold > self.config.over_threshold):
                self.control_client.add_experiment_problem(
                    control_pb2.ExperimentProblem.EP_FEEDBACK_NON_OPTIMAL)

            if self.publisher:
                self.publisher.send_msg(res)


# ---------- Feedback Correction ---------- #
_INITIAL_PI_VALUE = 1.00
_STANDARD_PI_UPDATE_FACTOR = 1.2
_FINE_PI_UPDATE_FACTOR = 1.02
_STABILITY_MARGIN = 0.7  # 70% over value where ringing occurs


class CorrectionState(Enum):
    """State of FeedbackCorrector."""

    NOT_RUNNING = auto()
    INIT = auto()
    CORRECT_I_STD = auto()
    CORRECT_I_FINE = auto()
    CORRECT_P_STD = auto()
    CORRECT_P_FINE = auto()
    REVERT_SCAN_PARAMS = auto()
    REMOVE_PROBLEM = auto()

    def next(self):
        """Jump to next state."""
        value = self.value + 1
        if value >= CorrectionState.REMOVE_PROBLEM.value:
            value = 1
        return CorrectionState(value)


# Helper groupings
_SINGLE_STEP_STATES = [CorrectionState.NOT_RUNNING,
                       CorrectionState.INIT,
                       CorrectionState.REVERT_SCAN_PARAMS]
_CORRECTION_STATES = [CorrectionState.CORRECT_I_STD,
                      CorrectionState.CORRECT_I_FINE,
                      CorrectionState.CORRECT_P_STD,
                      CorrectionState.CORRECT_P_FINE]
_STD_STATES = [CorrectionState.CORRECT_I_STD, CorrectionState.CORRECT_P_STD]
_FINE_STATES = [CorrectionState.CORRECT_I_FINE, CorrectionState.CORRECT_P_FINE]
_INTEGRAL_STATES = [CorrectionState.CORRECT_I_STD,
                    CorrectionState.CORRECT_I_FINE]


@dataclass
class CorrectionConfig:
    """Configuration parameters for feedback correction."""

    pi_inits: (float, float) = None  # If None, will not set on start
    pi_updates_std: (float, float) = (_STANDARD_PI_UPDATE_FACTOR,
                                      _STANDARD_PI_UPDATE_FACTOR)
    pi_updates_fine: (float, float) = (_FINE_PI_UPDATE_FACTOR,
                                       _FINE_PI_UPDATE_FACTOR)

    pi_stability_margins: (float, float) = (_STABILITY_MARGIN,
                                            _STABILITY_MARGIN)
    num_scan_lines: int = None  # If None, use original num lines
    points_per_line: int = None  # If None, use original points


class FeedbackCorrector(ScanningComponent):
    """Tries to correct ZCtrl feedback settings when improper.

    This component will do nothing until EP_FEEDBACK_NON_OPTIMAL is logged. At
    that point, it attempts to take over the MicroscopeTranslator, running scans
    while modifying the ZCtrl feedback parameters. It's goal is to optimize
    these feedback parameters according to the cited paper (see top).Once it
    has properly optimized the parameters, it removes the logged
    EP_FEEDBACK_NON_OPTIMAL, allowing whatever scanning logic to continue.

    In order to function, it expects to receive FeedbackAnalysis data from
    a FeedbackAnalyzer, as well as standard info from the MicroscopeTranslator.

    Note that it is the FeedbackAnalyzer that logs the problem, and this
    FeedbackCorrector that removes it.

    Args:
        _aconfig: Feedback Analysis config (for threshold params).
        _cconfig: Feedback Correction config.
        _correction_state: current state.
        _zctrl_params: latest ZCtrl Feedback parameters to send.
        _orig_scan_params: ScanParameters2d before we took control.
        _has_feedback_problem: whether or not the feedback problem is still
            logged.
    """

    def __init__(self, analysis_config: AnalysisConfig,
                 correction_config: CorrectionConfig,
                 rerun_wait_s: int, **kwargs):
        self._aconfig = analysis_config
        self._cconfig = correction_config

        self._correction_state = CorrectionState.NOT_RUNNING
        self._zctrl_params = None
        self._orig_scan_params = None
        self._has_feedback_problem = False

        super().__init__(rerun_wait_s,
                         self._next_scan_params, {'self': self},
                         control_pb2.ControlMode.CM_PROBLEM,
                         **kwargs)

    @staticmethod
    def _get_desired_scan_params(orig_params: scan_pb2.ScanParameters2d,
                                 config: CorrectionConfig):
        """Get desired scan parameters based on config."""
        params = copy.deepcopy(orig_params)
        if config.num_scan_lines:
            params.spatial.roi.size.y = (config.num_scan_lines *
                                         params.spatial.roi.size.y /
                                         params.data.shape.y)
            params.data.shape.y = config.num_scan_lines
        return params

    def _next_scan_params(self) -> scan_pb2.ScanParameters2d | None:
        """Logic sent to ScanHandler, which handles sending scans.

        This method returns None if we are not *ready* to start a scan,
        meaning no problem has been logged and/or we have not received the
        analysis for the latest scan.

        We are using ScanHandler's timing logic to sleep when a send fails:
        this is why we return None when not ready.

        Note that this method *changes state*! It does so only for one state:
        REMOVE_PROBLEM. See _update_state() for more info.
        """
        # In addition to correction states, the state *before* (when we set
        # init params) and the state *after* (when we finalize the params)
        # are ones where we will be setting the ZCtrlParameters.
        set_zctrl_states = (_CORRECTION_STATES +
                            [CorrectionState.INIT,
                             CorrectionState.REVERT_SCAN_PARAMS])
        if self._correction_state in set_zctrl_states:
            logger.info(f"Sending ZCtrl Params: {self._zctrl_params}")
            rep = send_req_handle_ctrl(
                self.control_client,
                self.control_client.set_zctrl_params,
                {'zctrl_params': self._zctrl_params},
                control_pb2.ExperimentProblem.EP_FEEDBACK_NON_OPTIMAL)
            if rep == control_pb2.ControlResponse.REP_SUCCESS:
                if self._correction_state == CorrectionState.REVERT_SCAN_PARAMS:
                    logger.info("Sending original scan parameters: "
                                f"{self._orig_scan_params}")
                    return self._orig_scan_params
                params = self._get_desired_scan_params(self._orig_scan_params,
                                                       self._cconfig)
                logger.info(f"Sending new scan params: {params}")
                return params
        elif self._correction_state == CorrectionState.REMOVE_PROBLEM:
            logger.info("Removing experiment problem.")
            rep = self.control_client.remove_experiment_problem(
                control_pb2.ExperimentProblem.EP_FEEDBACK_NON_OPTIMAL)
            if rep == control_pb2.ControlResponse.REP_SUCCESS:
                self._reset_correction_state()
        logger.debug("Not ready to run scan, sending no params.")
        return None

    def _reset_correction_state(self):
        """Reset the correction state."""
        self._correction_state = CorrectionState.NOT_RUNNING
        self._zctrl_params = None  # TODO: Am I needed?

    def on_message_received(self, envelope: str, proto: Message):
        """Obtain info from sub, update our state."""
        if isinstance(proto, control_pb2.ControlState):
            self._has_feedback_problem = (
                control_pb2.ExperimentProblem.EP_FEEDBACK_NON_OPTIMAL in
                proto.problems_set)
        if isinstance(proto, scan_pb2.ScanParameters2d):
            # If we have not had a feedback problem, keep storing original
            # params. Once a problem arises, we are modifying the params, so
            # do not.
            if not self._has_feedback_problem:
                self._orig_scan_params = proto
        if isinstance(proto, feedback_pb2.ZCtrlParameters):
            self._zctrl_params = proto
        if isinstance(proto, feedback_pb2.FeedbackAnalysis):
            self._update_state(proto)  # TODO: Is it OK to do this *only* on analysis results?
        super().on_message_received(envelope, proto)

    def _update_state(self, feedback_analysis: feedback_pb2.FeedbackAnalysis):
        """Given latest feedback analysis update the state and zctrl params.

        This method determines the state we should be in, and sets the zctrl
        feedback parameters to use for the next scan. It is to be called when
        we have feedback analysis of the latest scan, i.e. this update occurs
        after a successful scan with the last set zctrl params.

        Note: this method *does not* switch out of the last state, i.e.
        REMOVE_PROBLEM. The reason is simple: once we are in that state, we are
        no longer setting zctrl params and setting scans, but simply clearing
        the ExperimentProblem that triggered this component to run. Because of
        this, that state change is handled in _next_scan_params.

        Args:
            feedback_analysis: Latest FeedbackAnalysis received.
        """
        # If feedback problem  not triggered or feedback OFF, do nothing.
        if (not self._has_feedback_problem or not self._zctrl_params or not
                self._zctrl_params.feedbackOn):
            self._reset_correction_state()
            return

        # For single-step states, we directly jump at the beginning.
        # E.g.: when first starting, we are going from NOT_RUNNING to
        # INIT; we just jump straight ahead (and set some parameters below).
        if self._correction_state in _SINGLE_STEP_STATES:
            self._correction_state = self._correction_state.next()
        logger.info(f"In state {self._correction_state}")

        # Now, we are in our current state. Decide if we go to next and
        # what params to set.
        params_to_set = copy.deepcopy(self._zctrl_params)
        if self._correction_state is CorrectionState.INIT:
            logger.info("Starting feedback correction.")
            if self._cconfig.pi_inits:
                logger.debug("Initializing feedback values to config.")
                params_to_set.proportionalGain = self._cconfig.pi_inits[0]
                params_to_set.integralGain = self._cconfig.pi_inits[1]
        elif self._correction_state in _CORRECTION_STATES:
            over_threshold = (feedback_analysis.proportionOverThreshold >
                              self._aconfig.over_threshold)
            was_integral_state = self._correction_state in _INTEGRAL_STATES
            if over_threshold:
                logger.info("Feedback over threshold, jumping to next state.")
                update_factors = self._cconfig.pi_stability_margins
                self._correction_state = self._correction_state.next()
            elif self._correction_state in _STD_STATES:
                logger.info("Feedback under threshold, setting std factor.")
                update_factors = self._cconfig.pi_updates_std
            else:  # Fine state
                logger.info("Feedback under threshold, setting fine factor.")
                update_factors = self._cconfig.pi_updates_fine

            if was_integral_state:
                params_to_set.integralGain *= update_factors[1]
            else:
                params_to_set.proportionalGain *= update_factors[0]

        self._zctrl_params = params_to_set
        logger.debug(f"Going to set params: {self._zctrl_params}")
