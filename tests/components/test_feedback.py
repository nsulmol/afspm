"""Test feedback logic."""

import time
import pytest
import logging
import numpy as np
from pathlib import Path
from os import sep
import threading
import zmq

from afspm.io.protos.generated import scan_pb2
from afspm.io.protos.generated import control_pb2

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

@pytest.fixture
def config():
    return feedback.FeedbackAnalysisConfig()


@pytest.fixture
def xarr():
    return ac.create_xarray_from_img_path(TEST_IMG_PATH)


@pytest.fixture
def scan(xarr):
    return ac.convert_xarray_to_scan_pb2(xarr)


def test_analyze_feedback(config, xarr, scan):
    logger.info("Validate we can analyze and provie basic feedback.")

    over_prop, under_prop= feedback.analyze_feedback_on_arr(xarr, config)
    assert over_prop
    assert under_prop

    fb_analysis = feedback.analyze_feedback_on_scan(scan, config)
    assert fb_analysis.proportionOverThreshold < 0.01
    assert fb_analysis.proportionUnderThreshold < 0.035


def test_visualize_feedback(config, xarr):
    logger.info("Validate we can visualize our feedback analysis.")
    config.visualize_analysis = True
    config.viz_block_plot = False
    assert feedback.analyze_feedback_on_arr(xarr, config)
