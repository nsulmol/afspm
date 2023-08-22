"""Tests main spawn logic."""

import pytest
import copy
from afspm import spawn


@pytest.fixture
def good_monitor_dict():
    return {
        'AfspmComponentsMonitor': {
            'loop_sleep_s': 5,
            'missed_beats_before_dead': 2
        }
    }


def test_get_monitor_parameters(good_monitor_dict):
    """Validate we can parse the monitor params properly."""
    res = spawn._get_monitor_parameters(good_monitor_dict)
    assert res[0] == (good_monitor_dict[spawn.MONITOR_KEY]
                      [spawn.MONITOR_LOOP_SLEEP_KEY])
    assert res[1] == (good_monitor_dict[spawn.MONITOR_KEY]
                      [spawn.MONITOR_BEATS_BEFORE_DEAD_KEY])

    bad_dict = copy.deepcopy(good_monitor_dict)
    del bad_dict[spawn.MONITOR_KEY][spawn.MONITOR_LOOP_SLEEP_KEY]
    with pytest.raises(KeyError):
        spawn._get_monitor_parameters(bad_dict)

    del bad_dict[spawn.MONITOR_KEY]
    with pytest.raises(KeyError):
        spawn._get_monitor_parameters(bad_dict)


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
    """Validate _prepare_dict_for_spawning() works as expected."""
    filter_params = [None, [component1_name], [component2_name],
                     [component1_name, component2_name]]

    for filter in filter_params:
        res_dict = spawn._prepare_dict_for_spawning(good_config_dict,
                                                    filter)
        filter = ([component1_name, component2_name] if filter is None
                  else filter)
        for key in filter:
            assert res_dict[key]['banana'] == good_config_dict['url']
            assert key in res_dict

    # Test KeyError case!
    del good_config_dict[component1_name][spawn.IS_COMPONENT_KEY]
    with pytest.raises(KeyError):
        res_dict = spawn._prepare_dict_for_spawning(good_config_dict,
                                                    [component1_name])
