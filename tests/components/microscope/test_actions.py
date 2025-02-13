"""Test the actions logic (principally ActionHandler)."""

import os
import sys
import logging
import pytest
from typing import Any, Callable
import tomli

from afspm.components.microscope import actions

logger = logging.getLogger(__name__)


# Add current path to PATH for tests
path = os.path.dirname(os.path.realpath(__file__)) + os.sep
sys.path.append(path)


CALL_COUNT = 0
LAST_CALL_UUID = None


# -------------------- Methods -------------------- #
def on_start_scan():
    global CALL_COUNT
    CALL_COUNT += 1


def on_start_scan_with_self(handler: actions.CallableWithSelfActionHandler):
    handler.call_count += 1


def load_config(config_str: str) -> dict:
    return tomli.loads(config_str)


class MyCallableActionHandler(actions.CallableActionHandler):
    def __init__(self, actions_config: dict):
        self.actions = {}
        self._build_actions(actions_config)


class MyCallableWithSelfActionHandler(actions.CallableWithSelfActionHandler):
    call_count = 0

    def __init__(self, actions_config: dict):
        self.actions = {}
        self._build_actions(actions_config)


class MyStringActionHandler(actions.StringActionHandler):
    last_call_uuid = None

    def __init__(self, actions_config: dict):
        self.actions = {}
        self._build_actions(actions_config)

    def request_action_spm(self, spm_uuid: str):
        self.last_call_uuid = spm_uuid


# -------------------- Fixtures -------------------- #
@pytest.fixture
def callable_config():
    return """
    start-scan = 'test_actions.on_start_scan'
    """


@pytest.fixture
def callable_with_self_config():
    return """
    start-scan = 'test_actions.on_start_scan_with_self'
    """


@pytest.fixture
def str_config():
    return """
    start-scan = 'bananer'
    """


# -------------------- Tests -------------------- #
def test_callable_handler(callable_config):
    logger.info('Check that Callables are supported with ' +
                'CallableActionHandler.')

    params_config = load_config(callable_config)
    callback_handler = MyCallableActionHandler(params_config)

    generic_action = actions.MicroscopeAction.START_SCAN
    assert CALL_COUNT == 0
    callback_handler.request_action(generic_action)
    assert CALL_COUNT == 1

    logger.info('If an unsupported action is fed, throws an error.')
    generic_action = actions.MicroscopeAction.STOP_SCAN
    with pytest.raises(actions.ActionNotSupportedError):
        callback_handler.request_action(generic_action)


def test_callable_with_self_handler(callable_with_self_config):
    logger.info('Check that Callables are supported with ' +
                'CallableWithSelfActionHandler.')

    params_config = load_config(callable_with_self_config)
    callback_handler = MyCallableWithSelfActionHandler(params_config)

    generic_action = actions.MicroscopeAction.START_SCAN
    assert callback_handler.call_count == 0
    callback_handler.request_action(generic_action)
    assert callback_handler.call_count == 1

    logger.info('If an unsupported action is fed, throws an error.')
    generic_action = actions.MicroscopeAction.STOP_SCAN
    with pytest.raises(actions.ActionNotSupportedError):
        callback_handler.request_action(generic_action)


def test_str_handler(str_config):
    logger.info('Check that Callables are supported with ' +
                'StringActionHandler.')

    params_config = load_config(str_config)
    callback_handler = MyStringActionHandler(params_config)

    generic_action = actions.MicroscopeAction.START_SCAN
    assert callback_handler.last_call_uuid is None
    callback_handler.request_action(generic_action)
    assert callback_handler.last_call_uuid == 'bananer'

    logger.info('If an unsupported action is fed, throws an error.')
    generic_action = actions.MicroscopeAction.STOP_SCAN
    with pytest.raises(actions.ActionNotSupportedError):
        callback_handler.request_action(generic_action)
