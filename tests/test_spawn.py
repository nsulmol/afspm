"""Tests main spawn logic."""

import pytest
import copy
from afspm import spawn
from afspm.utils.parser import expand_variables_in_dict


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
            spawn.IS_COMPONENT_KEY: True,
            'hello': 'world',
            key_to_expand: key_to_expand_from
        },
        component2_name: {
            spawn.IS_COMPONENT_KEY: True,
            'hola': 'mundo',
            key_to_expand: key_to_expand_from
        }
    }


def test_prepare_dict_for_spawning(good_config_dict, component1_name,
                                   component2_name):
    """Validate variable expansion and filtering works as expected."""
    filter_params = [None, [component1_name], [component2_name],
                     [component1_name, component2_name]]

    for filter in filter_params:
        expanded_dict = expand_variables_in_dict(good_config_dict)
        res_dict = spawn._filter_requested_components(expanded_dict, filter)
        filter = ([component1_name, component2_name] if filter is None
                  else filter)
        for key in filter:
            assert res_dict[key]['banana'] == good_config_dict['url']
            assert key in res_dict

    # Test KeyError case!
    del good_config_dict[component1_name][spawn.IS_COMPONENT_KEY]
    with pytest.raises(KeyError):
        expanded_dict = expand_variables_in_dict(good_config_dict)
        res_dict = spawn._filter_requested_components(expanded_dict,
                                                      [component1_name])
