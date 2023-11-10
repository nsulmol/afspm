"""Tests main spawn logic."""

import pytest
import copy
from afspm import spawn
from afspm.utils.parser import COMPONENT_KEY


@pytest.fixture
def good_monitor_dict():
    return {
        'AfspmComponentsMonitor': {
            'loop_sleep_s': 5,
            'missed_beats_before_dead': 2
        }
    }


def test_set_up_logging():
    b = 2


@pytest.fixture
def component1_name():
    return 'Component1'


@pytest.fixture
def component2_name():
    return 'Component2'


@pytest.fixture
def key_to_expand():
    return 'banana'


@pytest.fixture
def key_to_expand_from():
    return 'url'


@pytest.fixture
def good_config_dict(component1_name, component2_name,
                     key_to_expand, key_to_expand_from):
    return {
        key_to_expand_from: 'inproc://banana',
        component1_name: {
            COMPONENT_KEY: True,
            'hello': 'world',
            key_to_expand: key_to_expand_from
        },
        component2_name: {
            COMPONENT_KEY: True,
            'hola': 'mundo',
            key_to_expand: key_to_expand_from
        }
    }

@pytest.fixture
def filter_params(component1_name, component2_name):
    return [None, [component1_name], [component2_name],
            [component1_name, component2_name]]


def test_prepare_dict_for_spawning(good_config_dict, component1_name,
                                   component2_name, filter_params,
                                   key_to_expand, key_to_expand_from):
    """Validate variable expansion and filtering works as expected."""
    for filter in filter_params:
        test_dict = copy.deepcopy(good_config_dict)
        comp_dict, vars_dict = spawn._filter_components_and_vars(
            test_dict, filter)
        filter = ([component1_name, component2_name] if filter is None
                  else filter)
        assert key_to_expand_from in vars_dict
        assert key_to_expand_from not in comp_dict
        for key in filter:
            assert key in comp_dict

    # Test KeyError case!
    del good_config_dict[component1_name][COMPONENT_KEY]
    with pytest.raises(KeyError):
        comp_dict, vars_dict = spawn._filter_components_and_vars(
            good_config_dict, [component1_name])
