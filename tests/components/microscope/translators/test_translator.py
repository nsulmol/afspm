"""This test suite exists to validate microscope translator functionality.

In order to run it, you must:
1. Start up the scheduler, via a call like the following:
    poetry run spawn config.toml --components_to_spawn=['scheduler']
2. Start up the microscope translator you wish to test. This startup will be
implementation-specific (look at the readme for your translator).
3. Run these tests (backslash just to fit in line length limits):
    poetry run pytest $PATH_TO_TEST/test_translator.py --config_path \
    $CONFIG_FILE_PATH
Note the default config path is './config.toml'.

Your config file contains the parameters for our tests. See sample_config.toml
for guidance.

NOTES:
- Ideally, you should set your translator's scan parameters to scan quickly
before running these tests. For example, increase your scan speed and
decrease your ROI size to pass the test more quickly. This can be accomplished
by including SCAN_SPEED_KEY, PHYS_SIZE_KEY, and DATA_SHAPE_KEY keys in your
config, with appropriate values. See these variables below, or look at
sample_config.toml.
- We do not have accessors for spectroscopic parameters (as we are trying to
minimize the parameters needed to support). So, you will need to manually set
the spectroscopic parameters to smaller values if you want the test to run more
quickly.
"""

from typing import Optional
import logging
import copy
import time
import tomli
import pytest
import zmq

from google.protobuf.message import Message

from afspm.io import common
from afspm.io.pubsub.subscriber import Subscriber
from afspm.io.pubsub.logic import cache_logic as cl
from afspm.io.control.client import ControlClient

from afspm.components.microscope import params
from afspm.components.microscope import actions
from afspm.utils.log import LOGGER_ROOT
from afspm.utils import units
from afspm.utils.protobuf import check_equal

from afspm.io.protos.generated import scan_pb2
from afspm.io.protos.generated import control_pb2
from afspm.io.protos.generated import feedback_pb2
from afspm.io.protos.generated import spec_pb2


logger = logging.getLogger(LOGGER_ROOT + '.samples.testing.test_translator.' +
                           __name__)


# Constants for config
SCAN_SPEED_KEY = 'scan_speed_nm_s'
PHYS_SIZE_KEY = 'phys_size_nm'
DATA_SHAPE_KEY = 'data_shape'
REQUEST_TIMEOUT_MS_KEY = 'request_timeout_ms'
# The relative tolerance used to compare set/get params (when float).
FLOAT_TOLERANCE_KEY = 'float_tolerance'


# -------------------- Fixtures -------------------- #
@pytest.fixture(scope="session")
def config_dict(config_path):
    with open(config_path, 'rb') as file:
        return tomli.load(file)


# --- General / Urls --- #
@pytest.fixture
def ctx():
    return zmq.Context.instance()


@pytest.fixture(scope="module")
def component_name():
    return "TestComponent"


@pytest.fixture(scope="module")
def timeout_ms(config_dict):
    return config_dict['timeout_ms']


@pytest.fixture(scope="module")
def move_wait_ms(config_dict):
    return config_dict['move_wait_ms']


@pytest.fixture(scope="module")
def spec_wait_ms(config_dict):
    return config_dict['spec_wait_ms']


@pytest.fixture(scope="module")
def scan_wait_ms(config_dict):
    return config_dict['scan_wait_ms']


@pytest.fixture(scope="module")
def psc_url(config_dict):
    return config_dict['psc_url']


@pytest.fixture(scope="module")
def router_url(config_dict):
    return config_dict['router_url']


@pytest.fixture(scope="module")
def exp_problem():
    return control_pb2.ExperimentProblem.EP_NONE


@pytest.fixture(scope="module")
def control_mode():
    return control_pb2.ControlMode.CM_AUTOMATED


@pytest.fixture(scope="module")
def default_control_state(control_mode):
    cs = control_pb2.ControlState()
    cs.control_mode = control_mode
    return cs


@pytest.fixture(scope="module")
def request_timeout_ms(config_dict):
    if REQUEST_TIMEOUT_MS_KEY in config_dict:
        return config_dict[REQUEST_TIMEOUT_MS_KEY]
    return common.REQUEST_TIMEOUT_MS


@pytest.fixture(scope="module")
def float_tolerance(config_dict):
    if FLOAT_TOLERANCE_KEY in config_dict:
        return config_dict[FLOAT_TOLERANCE_KEY]
    return 1e-09  # Standard from math.isclose()


# Note: topics use 'base' CacheLogic, so we catch all messages of each
# type (even though our PSC is using PBCScanLogic to set the envelopes).
@pytest.fixture(scope="module")
def topics_scan():
    return [cl.CacheLogic.get_envelope_for_proto(scan_pb2.Scan2d())]


@pytest.fixture(scope="module")
def topics_scan_params():
    return [cl.CacheLogic.get_envelope_for_proto(scan_pb2.ScanParameters2d())]


@pytest.fixture(scope="module")
def topics_scope_state():
    return [cl.CacheLogic.get_envelope_for_proto(scan_pb2.ScopeStateMsg())]


@pytest.fixture(scope="module")
def topics_zctrl():
    return [cl.CacheLogic.get_envelope_for_proto(
        feedback_pb2.ZCtrlParameters())]


@pytest.fixture(scope="module")
def topics_probe_pos():
    return [cl.CacheLogic.get_envelope_for_proto(spec_pb2.ProbePosition())]


@pytest.fixture(scope="module")
def topics_spec():
    return [cl.CacheLogic.get_envelope_for_proto(spec_pb2.Spec1d())]


# --- I/O Classes (Subscribers, Clients) --- #
@pytest.fixture
def sub_scan(ctx, topics_scan, scan_wait_ms, psc_url):
    return Subscriber(psc_url,
                      topics_to_sub=topics_scan,
                      poll_timeout_ms=scan_wait_ms)


@pytest.fixture
def sub_scope_state(ctx, topics_scope_state, move_wait_ms, psc_url):
    return Subscriber(psc_url,
                      topics_to_sub=topics_scope_state,
                      poll_timeout_ms=move_wait_ms)


@pytest.fixture
def sub_scan_params(ctx, topics_scan_params, timeout_ms, psc_url):
    return Subscriber(psc_url,
                      topics_to_sub=topics_scan_params,
                      poll_timeout_ms=timeout_ms)


@pytest.fixture
def sub_zctrl(ctx, topics_zctrl, timeout_ms, psc_url):
    return Subscriber(psc_url,
                      topics_to_sub=topics_zctrl,
                      poll_timeout_ms=timeout_ms)


@pytest.fixture
def sub_probe_pos(ctx, topics_probe_pos, move_wait_ms, psc_url):
    return Subscriber(psc_url,
                      topics_to_sub=topics_probe_pos,
                      poll_timeout_ms=move_wait_ms)


@pytest.fixture
def sub_spec(ctx, topics_spec, spec_wait_ms, psc_url):
    return Subscriber(psc_url,
                      topics_to_sub=topics_spec,
                      poll_timeout_ms=spec_wait_ms)


@pytest.fixture
def client(ctx, component_name, router_url, request_timeout_ms):
    return ControlClient(router_url, ctx, component_name,
                         request_timeout_ms=2*request_timeout_ms)


# -------------------- Helper Methods -------------------- #
def assert_sub_received_proto(sub: Subscriber, proto: Message):
    """Confirm a message is received by a subscriber."""
    assert sub.poll_and_store()
    assert len(sub.cache[cl.CacheLogic.get_envelope_for_proto(proto)]) == 1
    assert (sub.cache[cl.CacheLogic.get_envelope_for_proto(proto)][0]
            == proto)


def stop_client(client: ControlClient):
    """Close client and wait a bit to ensure it closes.

    This appears to be an issue with ZMQ, where the client is not killed
    in time for the next test. This should not be an issue in the real world,
    as we should be restarting the full process whenever we set up a component.

    TODO: You should investigate this further. I hate magic sleeps :(.
    """
    client._close_client()
    del client  # Explicitly kill to avoid zmq weirdness.
    time.sleep(1)


def startup_grab_control(client: ControlClient,
                         exp_problem: control_pb2.ExperimentProblem):
    """Request and get control of the microscope translator."""
    rep = client.request_control(exp_problem)
    assert rep == control_pb2.ControlResponse.REP_SUCCESS


def end_test(client: ControlClient):
    """Release control at end of test."""
    rep = client.release_control()
    assert rep == control_pb2.ControlResponse.REP_SUCCESS


def assert_and_return_message(sub: Subscriber):
    """Assert that the subscriber received one message and return proto."""
    messages = sub.poll_and_store()
    assert len(messages) == 1
    return messages[0][1]


# ----- test_run_scan specific methods ----- #
def get_config_scan_speed(config_dict: dict,
                          client: ControlClient
                          ) -> list[float] | None:
    """Determines if we are initing scan speed (for a faster scan).

    Checks if a desired scan speed was provided via the config, and the client
    supports setting scan speed. If so, we return the desired speed and
    the current/init speed the microscope translator has set.

    Args:
        config_dict: configuration dictionary for our tests.
        client: ControlClient we use to query the MicroscopeTranslator.

    Returns:
        list[float], containing [init_val, desired_val] or [init_val]
            (if no scan speed key found).
            If we are unable to even get the init_val, we return None.
    """
    scan_speeds = []

    # Get init val
    param_msg = control_pb2.ParameterMsg(
        parameter=params.MicroscopeParameter.SCAN_SPEED)
    rep, init_scan_msg = client.request_parameter(param_msg)
    if rep != control_pb2.ControlResponse.REP_SUCCESS:
        logger.debug('Unable to get scan speed! Returning None.')
        return None

    init_val_nm = units.convert(float(init_scan_msg.value),
                                init_scan_msg.units, 'nm/s')
    scan_speeds.append(init_val_nm)

    # Get config val
    if SCAN_SPEED_KEY in config_dict:
        desired_param = config_dict[SCAN_SPEED_KEY]
        scan_speeds.append(desired_param)
    return scan_speeds


def get_config_phys_size_nm(config_dict: dict) -> Optional[list[float, float]]:
    """Returns desired physical size from config_dict, None if not set."""
    if PHYS_SIZE_KEY in config_dict:
        return config_dict[PHYS_SIZE_KEY]
    return None


def get_config_data_shape(config_dict: dict) -> Optional[list[int, int]]:
    """Returns desired data shape from config_dict, None if not set."""
    if DATA_SHAPE_KEY in config_dict:
        return config_dict[DATA_SHAPE_KEY]
    return None


def set_scan_speed(client: ControlClient, scan_speed_nm_s: float):
    param_msg = control_pb2.ParameterMsg(
        parameter=params.MicroscopeParameter.SCAN_SPEED,
        value=str(scan_speed_nm_s), units='nm/s')

    logger.info("Setting scan speed to desired: %s nm/s",
                scan_speed_nm_s)
    rep, __ = client.request_parameter(param_msg)
    assert rep == control_pb2.ControlResponse.REP_SUCCESS


def set_scan_params(client: ControlClient,
                    orig_params: scan_pb2.ScanParameters2d,
                    phys_size_nm: Optional[list[float, float]] = None,
                    data_shape: Optional[list[int, int]] = None
                    ) -> scan_pb2.ScanParameters2d:
    """Set scan params to provided vals, return set params."""
    desired_params = copy.deepcopy(orig_params)
    if phys_size_nm:
        desired_params.spatial.roi.size.x = phys_size_nm[0]
        desired_params.spatial.roi.size.y = phys_size_nm[1]
        desired_params.spatial.length_units = 'nm'
    if data_shape:
        desired_params.data.shape.x = data_shape[0]
        desired_params.data.shape.y = data_shape[1]

    logger.info("Setting scan params to: %s",
                desired_params)
    rep = client.set_scan_params(desired_params)
    assert rep == control_pb2.ControlResponse.REP_SUCCESS
    return desired_params


# -------------------- Tests -------------------- #
def test_cancel_scan(client, default_control_state,
                     sub_scan, sub_scope_state, timeout_ms,
                     exp_problem):
    logger.info("Validate we can start and cancel a scan.")
    startup_grab_control(client, exp_problem)

    logger.info("First, flush any scan we have in the cache, and validate "
                "that we have an initial scope state of SS_FREE.")
    scope_state_msg = scan_pb2.ScopeStateMsg(
        scope_state=scan_pb2.ScopeState.SS_FREE)

    # Checking no scan (hack around, make poll short for this).
    tmp_timeout_ms = sub_scan._poll_timeout_ms
    sub_scan._poll_timeout_ms = timeout_ms
    sub_scan.poll_and_store()
    sub_scan._poll_timeout_ms = tmp_timeout_ms  # Return to prior

    assert_sub_received_proto(sub_scope_state,
                              scope_state_msg)

    logger.info("Next, validate that we can start a scan and are notified "
                "scanning has begun.")
    rep = client.start_scan()

    scope_state_msg = scan_pb2.ScopeStateMsg(
        scope_state=scan_pb2.ScopeState.SS_SCANNING)
    assert rep == control_pb2.ControlResponse.REP_SUCCESS
    assert_sub_received_proto(sub_scope_state,
                              scope_state_msg)

    logger.info("Next, cancel the scan before it has finished, and ensure "
                "we are notified it has been cancelled (via an interruption).")
    rep = client.stop_scan()
    assert rep == control_pb2.ControlResponse.REP_SUCCESS

    scope_state_msg = scan_pb2.ScopeStateMsg(
        scope_state=scan_pb2.ScopeState.SS_INTERRUPTED)
    assert_sub_received_proto(sub_scope_state,
                              scope_state_msg)

    logger.info("Lastly, ensure we are notified the translator is free and no "
                "scans were received.")
    scope_state_msg = scan_pb2.ScopeStateMsg(
        scope_state=scan_pb2.ScopeState.SS_FREE)
    assert_sub_received_proto(sub_scope_state,
                              scope_state_msg)

    assert not sub_scope_state.poll_and_store()

    # Checking no scan (hack around, make poll short for this).
    tmp_timeout_ms = sub_scan._poll_timeout_ms
    sub_scan._poll_timeout_ms = timeout_ms
    assert not sub_scan.poll_and_store()
    sub_scan._poll_timeout_ms = tmp_timeout_ms  # Return to prior

    end_test(client)
    stop_client(client)


def test_scan_params(client, default_control_state,
                     sub_scan_params, sub_scope_state, exp_problem,
                     float_tolerance):
    logger.info("Validate we can set scan parameters.")
    startup_grab_control(client, exp_problem)

    logger.info("First, validate we have initial scan params (from the "
                "cache), and scope state is free.")
    initial_params = assert_and_return_message(sub_scan_params)

    scope_state_msg = scan_pb2.ScopeStateMsg(
        scope_state=scan_pb2.ScopeState.SS_FREE)
    assert_sub_received_proto(sub_scope_state,
                              scope_state_msg)

    # Modify scan params.
    modified_params = copy.deepcopy(initial_params)
    modified_params.spatial.roi.top_left.x *= 1.1
    modified_params.spatial.roi.top_left.y *= 1.1
    modified_params.spatial.roi.size.x *= 0.9
    modified_params.spatial.roi.size.y *= 0.9

    # Multiply data shape by 2. Some controllers can only deal in a subset
    # of data shapes (usually powers of 2). This is a test that should pass
    # for all controllers.
    modified_params.data.shape.x *= 2
    modified_params.data.shape.y *= 2

    logger.info("Next, set new scan params. We expect a success.")
    rep = client.set_scan_params(modified_params)
    assert rep == control_pb2.ControlResponse.REP_SUCCESS

    logger.info("Next, validate that our subscriber receives these new "
                "params.")
    last_params = assert_and_return_message(sub_scan_params)

    logger.info('Requested new position. Expect scope state change.')
    scope_state_msg = scan_pb2.ScopeStateMsg(
        scope_state=scan_pb2.ScopeState.SS_MOVING)
    assert_sub_received_proto(sub_scope_state,
                              scope_state_msg)

    logger.info('Next, we should become free (stopped moving).')
    scope_state_msg = scan_pb2.ScopeStateMsg(
        scope_state=scan_pb2.ScopeState.SS_FREE)
    assert_sub_received_proto(sub_scope_state,
                              scope_state_msg)

    assert check_equal(last_params, modified_params, float_tolerance)

    logger.info("Now, return to our initial parameters.")
    rep = client.set_scan_params(initial_params)
    assert rep == control_pb2.ControlResponse.REP_SUCCESS
    last_params = assert_and_return_message(sub_scan_params)

    assert check_equal(last_params, initial_params, float_tolerance)

    end_test(client)
    stop_client(client)


def setup_faster_scan(config_dict: dict, client: ControlClient,
                      sub_scan_params: Subscriber,
                      ) -> (list[float], list[scan_pb2.ScanParameters2d]):
    """Set up a faster scan, if params were provided.

    This is a quite-ugly method, I'm sorry about that. But it will store
    the original scan speed and scan params, and then change these parameters
    to those requested in the config file (if any are provided). In the end,
    it will return a list of scan speeds and one of scan params. If no 'faster'
    scan speed or scan params are provided, the list will be of size 1 and only
    contain the original.

    Args:
        config_dict: configuration file, as a dict.
        client: ControlClient used to communicate with microscope.
        sub_scan_params: Subscriber to ScanParameters2d.

    Returns:
        list of [original_scan_speed, current_scan_speed] (only containing
            original_scan_speed if we did not change).
        list of [original_scan_params, current_scan_params] (only containing
            original_scan_params if we did not change).
    """
    logger.info("Check if we provided specific scan parameters "
                "(so the scan is not super long)")
    scan_speeds = get_config_scan_speed(config_dict, client)
    if len(scan_speeds) == 2:  # We received a desired speed, try to set
        set_scan_speed(client, scan_speeds[1])

    desired_phys_size_nm = get_config_phys_size_nm(config_dict)
    desired_data_shape = get_config_data_shape(config_dict)

    orig_scan_params = assert_and_return_message(sub_scan_params)
    scan_paramses = [orig_scan_params]  # Yuck, what a name -- sorry!
    if desired_phys_size_nm or desired_data_shape:
        desired_params = set_scan_params(client, orig_scan_params,
                                         desired_phys_size_nm,
                                         desired_data_shape)
        scan_paramses.append(desired_params)
    return scan_speeds, scan_paramses


def revert_original_scan_settings(
        client: ControlClient,
        orig_scan_speed: float | None,
        orig_scan_params: scan_pb2.ScanParameters2d | None):
    """Return scan speed / scan params to original values.

    Basically, we reset the scan speed and scan params to their 'original'
    values, where these values are provided as input arguments to the method.

    Args:
        client: ControlClient used to communicate with microscope.
        orig_scan_speed: original scan speed we wish to return to.
        orig_scan_params: original ScanParameters2d we wish to return to.
    """
    if orig_scan_speed or orig_scan_params:
        logger.info("Reset scan settings to what they were before the test.")
        if orig_scan_speed:
            set_scan_speed(client, orig_scan_speed)
        if orig_scan_params:
            set_scan_params(client, orig_scan_params)


def test_run_scan(client, default_control_state,
                  sub_scan, sub_scope_state, sub_scan_params, timeout_ms,
                  exp_problem, config_dict):
    logger.info("Validate we can start a scan, and receive one on finish.")
    startup_grab_control(client, exp_problem)
    scan_speeds, scan_paramses = setup_faster_scan(config_dict, client,
                                                   sub_scan_params)

    logger.info("Flush any scan we have in the cache, and validate "
                "that we have an initial scope state of SS_FREE.")
    scope_state_msg = scan_pb2.ScopeStateMsg(
        scope_state=scan_pb2.ScopeState.SS_FREE)

    # Hack around, make poll short for this.
    tmp_timeout_ms = sub_scan._poll_timeout_ms
    sub_scan._poll_timeout_ms = timeout_ms
    sub_scan.poll_and_store()
    assert_sub_received_proto(sub_scope_state,
                              scope_state_msg)
    sub_scan._poll_timeout_ms = tmp_timeout_ms  # Return to prior

    logger.info("Validate that we can start a scan and are notified "
                "scanning has begun.")
    rep = client.start_scan()
    scope_state_msg = scan_pb2.ScopeStateMsg(
        scope_state=scan_pb2.ScopeState.SS_SCANNING)
    assert rep == control_pb2.ControlResponse.REP_SUCCESS
    assert_sub_received_proto(sub_scope_state, scope_state_msg)

    logger.info("Wait for a predetermined 'long-enough' period, "
                "and validate the scan finishes.")
    assert sub_scan.poll_and_store()
    scope_state_msg.scope_state = scan_pb2.ScopeState.SS_FREE
    assert_sub_received_proto(sub_scope_state, scope_state_msg)

    logger.info("At the end, return to our initial parameters.")
    init_scan_speed = (scan_speeds[0] if scan_speeds and
                       len(scan_speeds) == 2 else None)
    init_scan_params = (scan_paramses[0] if scan_paramses and
                        len(scan_paramses) == 2 else None)
    revert_original_scan_settings(client, init_scan_speed, init_scan_params)
    end_test(client)
    stop_client(client)


def test_zctrl(client, default_control_state,
               sub_zctrl, timeout_ms,
               exp_problem, float_tolerance):
    logger.info("Validate we recieve and can set ZCtrlParams.")
    startup_grab_control(client, exp_problem)

    logger.info("First, ensure we receive initial ZCtrlParams.")
    initial_params = assert_and_return_message(sub_zctrl)

    modified_params = copy.deepcopy(initial_params)
    modified_params.proportionalGain *= 1.1
    modified_params.integralGain *= 1.1

    logger.info("Next, set new ZCtrlParams. We expect a success.")
    rep = client.set_zctrl_params(modified_params)
    assert rep == control_pb2.ControlResponse.REP_SUCCESS

    logger.info("Next, validate that our subscriber receives these new "
                "params.")
    last_params = assert_and_return_message(sub_zctrl)
    assert check_equal(last_params, modified_params, float_tolerance)

    logger.info("Now, return to our initial parameters.")
    rep = client.set_zctrl_params(initial_params)
    assert rep == control_pb2.ControlResponse.REP_SUCCESS
    last_params = assert_and_return_message(sub_zctrl)
    assert check_equal(last_params, initial_params, float_tolerance)

    end_test(client)
    stop_client(client)


def test_probe_pos(client, default_control_state,
                   sub_probe_pos, sub_scope_state,
                   exp_problem, float_tolerance):
    logger.info("Validate we recieve and can set ProbePosition.")
    startup_grab_control(client, exp_problem)

    logger.info("First, ensure we receive initial ProbePosition "
                "and scope state is free.")
    initial_probe_pos = assert_and_return_message(sub_probe_pos)
    scope_state_msg = scan_pb2.ScopeStateMsg(
        scope_state=scan_pb2.ScopeState.SS_FREE)
    assert_sub_received_proto(sub_scope_state,
                              scope_state_msg)

    modified_probe_pos = copy.deepcopy(initial_probe_pos)
    modified_probe_pos.point.x *= 0.9
    modified_probe_pos.point.y *= 0.9

    logger.info("Next, set new ProbePosition. We expect a success.")
    rep = client.set_probe_pos(modified_probe_pos)
    assert rep == control_pb2.ControlResponse.REP_SUCCESS

    logger.info("Next, validate that our subscriber receives these new "
                "params.")
    last_probe_pos = assert_and_return_message(sub_probe_pos)
    assert check_equal(last_probe_pos, modified_probe_pos, float_tolerance)

    logger.info('Requested new position. Expect scope state change.')
    scope_state_msg = scan_pb2.ScopeStateMsg(
        scope_state=scan_pb2.ScopeState.SS_MOVING)
    assert_sub_received_proto(sub_scope_state,
                              scope_state_msg)

    logger.info('Next, we should become free (stopped moving).')
    scope_state_msg = scan_pb2.ScopeStateMsg(
        scope_state=scan_pb2.ScopeState.SS_FREE)
    assert_sub_received_proto(sub_scope_state,
                              scope_state_msg)

    logger.info("Now, return to our initial parameters.")
    rep = client.set_probe_pos(initial_probe_pos)
    assert rep == control_pb2.ControlResponse.REP_SUCCESS
    last_probe_pos = assert_and_return_message(sub_probe_pos)
    assert check_equal(last_probe_pos, initial_probe_pos, float_tolerance)

    end_test(client)
    stop_client(client)


def test_cancel_spec(client, default_control_state,
                     sub_spec, sub_scope_state, timeout_ms,
                     exp_problem):
    logger.info("Validate we can start and cancel a spec collection.")
    startup_grab_control(client, exp_problem)

    logger.info("First, flush any spec we have in the cache, and validate "
                "that we have an initial scope state of SS_FREE.")
    scope_state_msg = scan_pb2.ScopeStateMsg(
        scope_state=scan_pb2.ScopeState.SS_FREE)

    # Checking no spec (hack around, make poll short for this).
    tmp_timeout_ms = sub_spec._poll_timeout_ms
    sub_spec._poll_timeout_ms = timeout_ms
    assert not sub_spec.poll_and_store()
    sub_spec._poll_timeout_ms = tmp_timeout_ms  # Return to prior

    assert_sub_received_proto(sub_scope_state,
                              scope_state_msg)

    logger.info("Next, validate that we can start a collection and are "
                "notified spec collection has begun.")
    rep = client.start_spec()

    scope_state_msg = scan_pb2.ScopeStateMsg(
        scope_state=scan_pb2.ScopeState.SS_SPEC)
    assert rep == control_pb2.ControlResponse.REP_SUCCESS
    assert_sub_received_proto(sub_scope_state,
                              scope_state_msg)

    logger.info("Next, cancel the collection before it has finished, and "
                "ensure we are notified it has been cancelled (via "
                "an interruption).")
    rep = client.stop_spec()
    assert rep == control_pb2.ControlResponse.REP_SUCCESS

    scope_state_msg = scan_pb2.ScopeStateMsg(
        scope_state=scan_pb2.ScopeState.SS_INTERRUPTED)
    assert_sub_received_proto(sub_scope_state,
                              scope_state_msg)

    logger.info("Lastly, ensure we are notified the translator is free and no "
                "scans were received.")
    scope_state_msg = scan_pb2.ScopeStateMsg(
        scope_state=scan_pb2.ScopeState.SS_FREE)
    assert_sub_received_proto(sub_scope_state,
                              scope_state_msg)

    assert not sub_scope_state.poll_and_store()

    # Checking no spec (hack around, make poll short for this).
    tmp_timeout_ms = sub_spec._poll_timeout_ms
    sub_spec._poll_timeout_ms = timeout_ms
    assert not sub_spec.poll_and_store()
    sub_spec._poll_timeout_ms = tmp_timeout_ms  # Return to prior

    end_test(client)
    stop_client(client)


def test_run_spec(client, default_control_state,
                  sub_spec, sub_scope_state, sub_probe_pos, timeout_ms,
                  exp_problem):
    logger.info("Validate we can start a spec collection, and receive one "
                + "on finish.")
    startup_grab_control(client, exp_problem)

    logger.info("Flush any spec we have in the cache, and validate "
                "that we have an initial scope state of SS_FREE.")
    scope_state_msg = scan_pb2.ScopeStateMsg(
        scope_state=scan_pb2.ScopeState.SS_FREE)

    # Hack around, make poll short for this.
    tmp_timeout_ms = sub_spec._poll_timeout_ms
    sub_spec._poll_timeout_ms = timeout_ms
    assert not sub_spec.poll_and_store()
    assert_sub_received_proto(sub_scope_state,
                              scope_state_msg)
    sub_spec._poll_timeout_ms = tmp_timeout_ms  # Return to prior

    logger.info("Validate that we can start a spec collection and  "
                "are notified collection has begun.")
    rep = client.start_spec()
    scope_state_msg = scan_pb2.ScopeStateMsg(
        scope_state=scan_pb2.ScopeState.SS_SPEC)
    assert rep == control_pb2.ControlResponse.REP_SUCCESS
    assert_sub_received_proto(sub_scope_state, scope_state_msg)

    logger.info("Wait for a predetermined 'long-enough' period, "
                "and validate the spec finishes.")
    assert sub_spec.poll_and_store()
    scope_state_msg.scope_state = scan_pb2.ScopeState.SS_FREE
    assert_sub_received_proto(sub_scope_state, scope_state_msg)

    end_test(client)
    stop_client(client)


# ----- 'Full Loop' Tests (on scans and specs) ----- #
def test_scan_coords(client, default_control_state,
                     sub_scan_params, sub_scope_state, sub_scan,
                     exp_problem, float_tolerance, config_dict):
    logger.info('Validate our read scan contains the physical region.')
    logger.info('This test will fail if test_run_scan and test_scan_params '
                'fail.')
    startup_grab_control(client, exp_problem)
    # Set up faster scan params / speeds if in config.
    scan_speeds, scan_paramses = setup_faster_scan(config_dict, client,
                                                   sub_scan_params)
    scope_state_msg = scan_pb2.ScopeStateMsg(
        scope_state=scan_pb2.ScopeState.SS_FREE)
    assert_sub_received_proto(sub_scope_state,
                              scope_state_msg)

    # --- Modify scan params --- #
    # Grab the last params in scan_paramses, which is either the original
    # ones or what we changed them to in order to scan faster.
    modified_params = copy.deepcopy(scan_paramses[-1])
    modified_params.spatial.roi.top_left.x = (
        modified_params.spatial.roi.size.x * 0.25)
    modified_params.spatial.roi.top_left.y = (
        modified_params.spatial.roi.size.y * 0.25)
    modified_params.spatial.roi.size.x *= 0.5
    modified_params.spatial.roi.size.y *= 0.5

    rep = client.set_scan_params(modified_params)
    assert rep == control_pb2.ControlResponse.REP_SUCCESS

    last_params = assert_and_return_message(sub_scan_params)
    assert check_equal(last_params, modified_params, float_tolerance)

    scope_state_msg = scan_pb2.ScopeStateMsg(
        scope_state=scan_pb2.ScopeState.SS_MOVING)
    assert_sub_received_proto(sub_scope_state,
                              scope_state_msg)
    scope_state_msg = scan_pb2.ScopeStateMsg(
        scope_state=scan_pb2.ScopeState.SS_FREE)
    assert_sub_received_proto(sub_scope_state,
                              scope_state_msg)

    # --- Perform a scan --- #
    # Hack around, make poll short for this.
    tmp_timeout_ms = sub_scan._poll_timeout_ms
    sub_scan._poll_timeout_ms = timeout_ms
    sub_scan.poll_and_store()
    assert_sub_received_proto(sub_scope_state,
                              scope_state_msg)
    sub_scan._poll_timeout_ms = tmp_timeout_ms  # Return to prior

    rep = client.start_scan()
    scope_state_msg = scan_pb2.ScopeStateMsg(
        scope_state=scan_pb2.ScopeState.SS_SCANNING)
    assert rep == control_pb2.ControlResponse.REP_SUCCESS
    assert_sub_received_proto(sub_scope_state, scope_state_msg)

    logger.info("Wait for a predetermined 'long-enough' period, "
                "and validate the scan finishes.")
    # Ensure the scan params in the Scan2d match those we sent!
    # NOTE: this is not a good practice if using CSCorrectedSchedulder,
    # as it will be 'correcting' for drift and thus you cannot guarantee
    # a perfect match. But for this experiment (where we have the base
    # scheduler), it is ok.
    scan = assert_and_return_message(sub_scan)
    assert scan.params == modified_params

    scope_state_msg.scope_state = scan_pb2.ScopeState.SS_FREE
    assert_sub_received_proto(sub_scope_state, scope_state_msg)

    # --- Cleanup --- #
    logger.info("At the end, return to our initial parameters.")
    init_scan_speed = (scan_speeds[0] if scan_speeds and
                       len(scan_speeds) == 2 else None)
    init_scan_params = (scan_paramses[0] if scan_paramses and
                        len(scan_paramses) == 2 else None)
    revert_original_scan_settings(client, init_scan_speed, init_scan_params)

    end_test(client)
    stop_client(client)


def test_spec_coords(client, default_control_state,
                     sub_probe_pos, sub_spec, sub_scope_state,
                     sub_scan_params, exp_problem, float_tolerance):
    logger.info('Validate our read spec contains the physical position.')
    logger.info('This test will fail if test_run_spec and test_probe_pos fail.')

    startup_grab_control(client, exp_problem)

    # --- Setup --- #
    logger.info("First, ensure we receive initial ProbePosition, "
                "ScanParameters2d and that scope state is free.")
    initial_probe_pos = assert_and_return_message(sub_probe_pos)
    scope_state_msg = scan_pb2.ScopeStateMsg(
        scope_state=scan_pb2.ScopeState.SS_FREE)
    assert_sub_received_proto(sub_scope_state,
                              scope_state_msg)
    scan_params = assert_and_return_message(sub_scan_params)

    # Configure probe pos we want (we use scan_params to get the
    # size of the scan region and set a position based on this).
    modified_probe_pos = copy.deepcopy(initial_probe_pos)
    modified_probe_pos.point.x = (
        scan_params.spatial.roi.size.x * 0.25)
    modified_probe_pos.point.y = (
        scan_params.spatial.roi.size.y * 0.25)

    # Checking no spec (hack around, make poll short for this).
    tmp_timeout_ms = sub_spec._poll_timeout_ms
    sub_spec._poll_timeout_ms = timeout_ms
    assert not sub_spec.poll_and_store()
    sub_spec._poll_timeout_ms = tmp_timeout_ms  # Return to prior

    assert_sub_received_proto(sub_scope_state,
                              scope_state_msg)

    # --- Perform Spec --- #
    logger.info("Validate that we can start a spec collection and  "
                "are notified collection has begun.")
    rep = client.start_spec()
    scope_state_msg = scan_pb2.ScopeStateMsg(
        scope_state=scan_pb2.ScopeState.SS_SPEC)
    assert rep == control_pb2.ControlResponse.REP_SUCCESS
    assert_sub_received_proto(sub_scope_state, scope_state_msg)

    logger.info("Wait for a predetermined 'long-enough' period, "
                "and validate the spec finishes.")
    spec = assert_and_return_message(sub_spec)
    assert spec.position == modified_probe_pos

    scope_state_msg.scope_state = scan_pb2.ScopeState.SS_FREE
    assert_sub_received_proto(sub_scope_state, scope_state_msg)

    # --- Tear Down --- #
    logger.info("Now, return to our initial parameters.")
    rep = client.set_probe_pos(initial_probe_pos)
    assert rep == control_pb2.ControlResponse.REP_SUCCESS
    last_probe_pos = assert_and_return_message(sub_probe_pos)
    assert check_equal(last_probe_pos, initial_probe_pos, float_tolerance)

    end_test(client)
    stop_client(client)


# ----- Checking actions and parameters support ----- #
def test_parameters(client, exp_problem):
    logger.info("Check which parameters are supported via REQ_PARAM.")
    startup_grab_control(client, exp_problem)

    logger.info("First, does the translator even support REQ_PARAM?")
    param = control_pb2.ParameterMsg(
        parameter=params.MicroscopeParameter.SCAN_TOP_LEFT_X)
    rep, rcvd_param = client.request_parameter(param)

    if rep == control_pb2.ControlResponse.REP_CMD_NOT_SUPPORTED:
        logger.warning("REQ_PARAM is not supported, exiting.")
        return

    logger.info("Testing individual parameters.")
    for param_name in params.PARAMETERS:
        param = control_pb2.ParameterMsg(parameter=param_name)
        rep, rcvd_param = client.request_parameter(param)

        if rep == control_pb2.ControlResponse.REP_SUCCESS:
            logger.info(f"Param {param_name} is supported.")
        elif rep == control_pb2.ControlResponse.REP_PARAM_NOT_SUPPORTED:
            logger.warning(f'Param {param_name} is not supported.')
        elif rep == control_pb2.ControlResponse.REP_PARAM_ERROR:
            logger.error(f'Param error requesting param {param_name}.')
        else:
            logger.error('Response %s for param %s',
                         common.get_enum_str(control_pb2.ControlResponse,
                                             rep), param_name)

    end_test(client)
    stop_client(client)


def test_actions(client, exp_problem):
    logger.info('Check which actions are supported via REQ_ACTION.')
    startup_grab_control(client, exp_problem)

    logger.info("Testing individual actions.")
    for action_name in actions.MicroscopeAction:
        action = control_pb2.ActionMsg(action=action_name)
        rep = client.check_action_support(action)

        if rep == control_pb2.ControlResponse.REP_SUCCESS:
            logger.info(f"Action {action_name} is supported.")
        elif rep == control_pb2.ControlResponse.REP_ACTION_NOT_SUPPORTED:
            logger.warning(f'Action {action_name} is not supported.')
        elif rep == control_pb2.ControlResponse.REP_ACTION_ERROR:
            logger.error(f'Action error requesting param {action_name} '
                         '(should not happen).')
        else:
            logger.error('Response %s for action %s',
                         common.get_enum_str(control_pb2.ControlResponse,
                                             rep), action_name)

    end_test(client)
    stop_client(client)
