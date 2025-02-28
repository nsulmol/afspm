"""Test the actions logic (principally ActionHandler)."""

import os
import sys
import logging
import pytest
import tomli

from afspm.utils.parser import consider_config_path
from afspm.components.microscope import actions

logger = logging.getLogger(__name__)


# Add current path to PATH for tests
consider_config_path(__file__)

CALL_COUNT = 0
LAST_CALL_UUID = None


# -------------------- Methods -------------------- #
def on_start_scan():
    global CALL_COUNT
    CALL_COUNT += 1


def on_start_scan_with_self(handler: actions.ActionHandler):
    handler.call_count += 1


def on_start_scan_str(uuid: str):
    global LAST_CALL_UUID
    LAST_CALL_UUID = uuid


def on_start_scan_str_with_self(handler: actions.ActionHandler,
                                uuid: str):
    handler.last_call_uuid = uuid


def load_config(config_str: str) -> dict:
    return tomli.loads(config_str)


class MyActionHandler(actions.ActionHandler):
    call_count = 0
    last_call_uuid = None

    def __init__(self, actions_config: dict):
        self.actions = {}
        self._build_actions(actions_config)


# -------------------- Fixtures -------------------- #
@pytest.fixture
def callable_config():
    return """
    [start-scan]
    method = 'test_actions.on_start_scan'
    """


@pytest.fixture
def callable_with_self_config():
    return """
    [start-scan]
    method = 'test_actions.on_start_scan_with_self'
    type = 'PASS_SELF'
    """


@pytest.fixture
def callable_str_config():
    return """
    [start-scan]
    method = 'test_actions.on_start_scan_str'
    uuid = 'HELLO'
    """


@pytest.fixture
def callable_str_with_self_config():
    return """
    [start-scan]
    method = 'test_actions.on_start_scan_str_with_self'
    type = 'PASS_SELF'
    uuid = 'HELLO'
    """


# -------------------- Tests -------------------- #
def test_callable_handler(callable_config):
    global CALL_COUNT
    logger.info('Check that Callables are supported with ' +
                'CallableActionHandler.')

    params_config = load_config(callable_config)
    callback_handler = MyActionHandler(params_config)

    generic_action = actions.MicroscopeAction.START_SCAN
    assert CALL_COUNT == 0
    callback_handler.request_action(generic_action)
    assert CALL_COUNT == 1

    logger.info('If an unsupported action is fed, throws an error.')
    generic_action = actions.MicroscopeAction.STOP_SCAN
    with pytest.raises(actions.ActionNotSupportedError):
        callback_handler.request_action(generic_action)

    # Reset
    CALL_COUNT = 0


def test_callable_with_self_handler(callable_with_self_config):
    logger.info('Check that Callables are supported with ' +
                'CallableWithSelfActionHandler.')

    params_config = load_config(callable_with_self_config)
    callback_handler = MyActionHandler(params_config)

    generic_action = actions.MicroscopeAction.START_SCAN
    assert callback_handler.call_count == 0
    callback_handler.request_action(generic_action)
    assert callback_handler.call_count == 1

    logger.info('If an unsupported action is fed, throws an error.')
    generic_action = actions.MicroscopeAction.STOP_SCAN
    with pytest.raises(actions.ActionNotSupportedError):
        callback_handler.request_action(generic_action)


def test_callable_str_handler(callable_str_config):
    global LAST_CALL_UUID
    logger.info('Check that Callables are supported with ' +
                'extra args (for NORMAL case).')

    params_config = load_config(callable_str_config)
    callback_handler = MyActionHandler(params_config)

    generic_action = actions.MicroscopeAction.START_SCAN
    assert LAST_CALL_UUID is None
    callback_handler.request_action(generic_action)
    assert LAST_CALL_UUID == 'HELLO'

    logger.info('If an unsupported action is fed, throws an error.')
    generic_action = actions.MicroscopeAction.STOP_SCAN
    with pytest.raises(actions.ActionNotSupportedError):
        callback_handler.request_action(generic_action)

    # Reset
    LAST_CALL_UUID = None


def test_callable_str_with_self_handler(callable_str_with_self_config):
    logger.info('Check that Callables are supported with ' +
                'extra args (for PASS_SELF case).')

    params_config = load_config(callable_str_with_self_config)
    callback_handler = MyActionHandler(params_config)

    generic_action = actions.MicroscopeAction.START_SCAN
    assert callback_handler.last_call_uuid is None
    callback_handler.request_action(generic_action)
    assert callback_handler.last_call_uuid == 'HELLO'

    logger.info('If an unsupported action is fed, throws an error.')
    generic_action = actions.MicroscopeAction.STOP_SCAN
    with pytest.raises(actions.ActionNotSupportedError):
        callback_handler.request_action(generic_action)
