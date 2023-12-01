"""Test feedback logic."""

import time
import copy
import pytest
import logging
import numpy as np
from pathlib import Path
from os import sep
import threading
import zmq

from afspm.io.protos.generated import scan_pb2
from afspm.io.protos.generated import control_pb2
from afspm.io.protos.generated import feedback_pb2

from afspm.io.control.client import ControlClient
from afspm.io.control.server import ControlServer
from afspm.io.pubsub.publisher import Publisher
from afspm.io.pubsub.subscriber import Subscriber

from afspm.io import common
from afspm.components import feedback
from afspm.utils import array_converters as ac


logger = logging.getLogger(__name__)


TEST_IMG_PATH = (str(Path(__file__).parent.parent.resolve()) + sep + "data" +
                     sep + "sample.png")

# ---------- Test Feedback Analysis --------- #
@pytest.fixture
def analysis_config():
    return feedback.AnalysisConfig()


@pytest.fixture
def xarr():
    return ac.create_xarray_from_img_path(TEST_IMG_PATH)


@pytest.fixture
def scan(xarr):
    return ac.convert_xarray_to_scan_pb2(xarr)


def test_analyze_feedback(analysis_config, xarr, scan):
    logger.info("Validate we can analyze and provie basic feedback.")

    over_prop, under_prop = feedback.analyze_feedback_on_arr(xarr,
                                                             analysis_config)
    assert over_prop
    assert under_prop

    fb_analysis = feedback.analyze_feedback_on_scan(scan, analysis_config)
    assert fb_analysis.proportionOverThreshold < 0.01
    assert fb_analysis.proportionUnderThreshold < 0.035


def test_visualize_feedback(analysis_config, xarr):
    logger.info("Validate we can visualize our feedback analysis.")
    analysis_config.visualize_analysis = True
    analysis_config.viz_block_plot = False
    assert feedback.analyze_feedback_on_arr(xarr, analysis_config)


# ---------- Test Feedback Correction --------- #
class FakeClient:
    def __init__(self):
        self.zctrl = None  # Holds latest zctrl params
        self.rep = control_pb2.ControlResponse.REP_SUCCESS

    def set_uuid(self, name: str):
        pass

    def set_zctrl_params(self, zctrl_params: feedback_pb2.ZCtrlParameters
                         ) -> control_pb2.ControlResponse:
        if self.rep == control_pb2.ControlResponse.REP_SUCCESS:
            self.zctrl = zctrl_params
        return self.rep

    def request_control(self, control_mode: control_pb2.ControlMode
                        ) -> control_pb2.ControlResponse:
        return self.rep

    def remove_experiment_problem(self, problem: control_pb2.ExperimentProblem,
                                  ) -> control_pb2.ControlResponse:
        return self.rep


@pytest.fixture
def cs_no_problem():
    return control_pb2.ControlState(
        control_mode=control_pb2.ControlMode.CM_AUTOMATED)

@pytest.fixture
def cs_with_problem():
    cs = control_pb2.ControlState(
        control_mode=control_pb2.ControlMode.CM_AUTOMATED)
    cs.problems_set.append(
        control_pb2.ExperimentProblem.EP_FEEDBACK_NON_OPTIMAL)
    return cs


@pytest.fixture
def zctrl_params():
    return feedback_pb2.ZCtrlParameters(feedbackOn=True,
                                        proportionalGain=1.0,
                                        integralGain=1.0)


@pytest.fixture
def scan_params():
    return common.create_scan_params_2d([50.0, -25.0],
                                        [2000.00, 3000.00],
                                        'nm', [100, 150],
                                        'nm')


@pytest.fixture
def fb_analysis_under():
    return feedback_pb2.FeedbackAnalysis(
        proportionOverThreshold=feedback._THRESHOLD_FACTOR,
        proportionUnderThreshold=0.0)


@pytest.fixture
def fb_analysis_over():
    return feedback_pb2.FeedbackAnalysis(
        proportionOverThreshold=feedback._THRESHOLD_FACTOR * 1.1,
        proportionUnderThreshold=0.0)


@pytest.fixture
def feedback_corrector():
    return feedback.FeedbackCorrector(feedback.AnalysisConfig(),
                                      feedback.CorrectionConfig(), 0,
                                      subscriber=None,
                                      control_client=FakeClient(),
                                      name='corrector')

def step_in_corrector(corrector: feedback.FeedbackCorrector,
                      init_state: feedback.CorrectionState,
                      scan_params: scan_pb2.ScanParameters2d = None,
                      control_state: control_pb2.ControlState = None,
                      zctrl_params: feedback_pb2.ZCtrlParameters = None,
                      feedback_analysis: feedback_pb2.FeedbackAnalysis = None):
    """Set corrector to provided state and then send messages."""
    corrector._correction_state = init_state
    if scan_params:
        corrector.on_message_received("", scan_params)
    if control_state:
        corrector.on_message_received("", control_state)
    if zctrl_params:
        corrector.on_message_received("", zctrl_params)
    if feedback_analysis:
        corrector.on_message_received("", feedback_analysis)
    corrector._next_scan_params()


def test_corrector_startup(cs_no_problem, cs_with_problem, feedback_corrector,
                           zctrl_params, fb_analysis_under):
    logger.info("Corrector doesn't begin until problem is presented "
                "and ZCtrl params received with feedback.")
    assert (feedback_corrector._correction_state ==
            feedback.CorrectionState.NOT_RUNNING)

    feedback_corrector.on_message_received("", cs_no_problem)
    feedback_corrector.on_message_received("", fb_analysis_under)
    assert (feedback_corrector._correction_state ==
            feedback.CorrectionState.NOT_RUNNING)

    zctrl_params.feedbackOn = False
    feedback_corrector.on_message_received("", zctrl_params)
    feedback_corrector.on_message_received("", fb_analysis_under)
    assert (feedback_corrector._correction_state ==
            feedback.CorrectionState.NOT_RUNNING)

    feedback_corrector.on_message_received("", cs_with_problem)
    feedback_corrector.on_message_received("", fb_analysis_under)
    assert (feedback_corrector._correction_state ==
            feedback.CorrectionState.NOT_RUNNING)

    zctrl_params.feedbackOn = True
    feedback_corrector.on_message_received("", zctrl_params)
    feedback_corrector.on_message_received("", fb_analysis_under)
    assert (feedback_corrector._correction_state ==
            feedback.CorrectionState.INIT)

    logger.debug("Also, valiate that if the problem is removed we stop.")
    feedback_corrector.on_message_received("", cs_no_problem)
    feedback_corrector.on_message_received("", fb_analysis_under)
    assert (feedback_corrector._correction_state ==
            feedback.CorrectionState.NOT_RUNNING)


def assert_corrector_sent_zctrl(feedback_corrector: feedback.FeedbackCorrector,
                                expected_pi_vals: list[float, float]):
    """Validate that the corrector-sent zctrl params match the expected."""
    assert (np.isclose(feedback_corrector.control_client.zctrl.proportionalGain,
                       expected_pi_vals[0]))
    assert (np.isclose(feedback_corrector.control_client.zctrl.integralGain,
                       expected_pi_vals[1]))


def test_zctrl_init(cs_with_problem, feedback_corrector,
                    zctrl_params, fb_analysis_under,
                    scan_params):
    logger.info("Ensure ZCtrl init state functions as expected.")
    step_in_corrector(feedback_corrector, feedback.CorrectionState.NOT_RUNNING,
                   scan_params, cs_with_problem,
                   zctrl_params, fb_analysis_under)
    assert (feedback_corrector._correction_state ==
            feedback.CorrectionState.INIT)

    logger.debug("With no pi_inits, we expect z ctrl params to match initials.")
    assert feedback_corrector._next_scan_params()
    assert (feedback_corrector.control_client.zctrl == zctrl_params)

    logger.debug("Providing pi_inits, we expect the params to change.")
    pi_inits = (0.5, 0.25)
    feedback_corrector._cconfig.pi_inits = pi_inits

    # Reset state
    feedback_corrector._correction_state = feedback.CorrectionState.NOT_RUNNING
    feedback_corrector.on_message_received("", fb_analysis_under)

    assert feedback_corrector._next_scan_params()
    assert_corrector_sent_zctrl(feedback_corrector, pi_inits)
    assert (feedback_corrector._correction_state ==
            feedback.CorrectionState.INIT)


def test_zctrl_params_over_states(cs_with_problem, feedback_corrector,
                                  zctrl_params, fb_analysis_under,
                                  fb_analysis_over, scan_params):
    logger.info("Validate we send proper zctrl params between states.")
    init_pi_vals = [1.0, 1.0]

    under_factors = [feedback._STANDARD_PI_UPDATE_FACTOR,  # CORRECT_I_STD
                     feedback._FINE_PI_UPDATE_FACTOR,  # CORRECT_I_FINE
                     feedback._STANDARD_PI_UPDATE_FACTOR,  # CORRECT_P_STD
                     feedback._FINE_PI_UPDATE_FACTOR]  # CORRECT_P_FINE
    pi_index = [1, 1, 0, 0]

    for state, under_factor, idx in zip(feedback._CORRECTION_STATES,
                                        under_factors, pi_index):
        logger.info("Testing PI values when in state %s",
                    feedback_corrector._correction_state)

        fb_analyses = [fb_analysis_under, fb_analysis_over]
        factors = [under_factor, feedback._STABILITY_MARGIN]
        expected_states = [state, state.next()]
        for fb_analysis, factor, expected_state in zip(fb_analyses, factors,
                                                       expected_states):
            step_in_corrector(feedback_corrector, state,
                           scan_params, cs_with_problem,
                           zctrl_params, fb_analysis)

            assert feedback_corrector._correction_state == expected_state
            expected_pi_vals = copy.deepcopy(init_pi_vals)
            expected_pi_vals[idx] *= factor
            logger.debug("Expected PI vals: %s, Feedback-sent vals: %s",
                         expected_pi_vals,
                         feedback_corrector.control_client.zctrl)
            assert_corrector_sent_zctrl(feedback_corrector, expected_pi_vals)


def test_scan_params_over_states(cs_with_problem, feedback_corrector,
                                 zctrl_params, fb_analysis_under,
                                 scan_params):
    logger.info("Validate that we send proper scan params between states.")
    # Init zctrl params: needed to run.
    feedback_corrector._zctrl_params = zctrl_params

    logger.info("We expect scan params to always be the same without"
                 "num_scan_lines or points_per_line")
    for state in list(feedback.CorrectionState):
        logger.info("Testing scan state %s", state)
        # Not sending zctrl_params or feedback analysis to not update state
        step_in_corrector(feedback_corrector, state,
                          scan_params, cs_with_problem,
                          None, None)
        if state in [feedback.CorrectionState.NOT_RUNNING,
                     feedback.CorrectionState.REMOVE_PROBLEM]:
            assert not feedback_corrector._next_scan_params()
        else:
            assert feedback_corrector._next_scan_params() == scan_params

    feedback_corrector._cconfig.num_scan_lines = 1
    feedback_corrector._cconfig.points_per_line = 25

    correction_params = copy.deepcopy(scan_params)
    correction_params.spatial.roi.size.y = (
        correction_params.spatial.roi.size.y / correction_params.data.shape.y)
    correction_params.data.shape.y = 1

    logger.info("We expect scan params to always be modified if in config "
                "states.")
    for state in list(feedback.CorrectionState):
        logger.info("Testing scan state %s", state)
        # Not sending zctrl_params or feedback analysis to not update state
        step_in_corrector(feedback_corrector, state,
                          scan_params, cs_with_problem,
                          None, None)
        if state in [feedback.CorrectionState.NOT_RUNNING,
                     feedback.CorrectionState.REMOVE_PROBLEM]:
            assert not feedback_corrector._next_scan_params()
        elif state == feedback.CorrectionState.REVERT_SCAN_PARAMS:
            assert feedback_corrector._next_scan_params() == scan_params
        else:
            assert feedback_corrector._next_scan_params() == correction_params
