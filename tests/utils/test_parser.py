"""Test parsing/populating methods."""

import pytest
import zmq
from afspm.utils import parser


@pytest.fixture
def sample_dict():
    return {
        'pub_url': 'tcp://127.0.0.1:5555',
        'publisher': {
            'url': 'pub_url',
            'get_envelope_given_proto':
            'afspm.io.cache.cache_logic.CacheLogic.create_envelope_from_proto'
        },
        'level3': {
            'publisher': {
                'url': 'pub_url',
                'get_envelope_given_proto':
                'afspm.io.cache.cache_logic.CacheLogic.create_envelope_from_proto'
            }
        }
    }

@pytest.fixture
def expected_expanded_dict():
    return {
        'pub_url': 'tcp://127.0.0.1:5555',
        'publisher': {
            'url': 'tcp://127.0.0.1:5555',
            'get_envelope_given_proto':
            'afspm.io.cache.cache_logic.CacheLogic.create_envelope_from_proto'
        },
        'level3': {
            'publisher': {
                'url': 'tcp://127.0.0.1:5555',
                'get_envelope_given_proto':
                'afspm.io.cache.cache_logic.CacheLogic.create_envelope_from_proto'
            }
        }
    }


def test_expand_variables(sample_dict, expected_expanded_dict):
    """Validate we are expanding variables out properly."""
    expanded_dict = parser.expand_variables_in_dict(sample_dict)
    assert expanded_dict == expected_expanded_dict


@pytest.fixture
def control_client_str():
    return 'afspm.io.control.control_client.ControlClient'


@pytest.fixture
def sample_url():
    return 'inproc://banana'


def test_import_from_string(control_client_str, sample_url):
    """Validate class importing ability."""

    # First, confirm we can import when the module path is provided properly.
    #control_client_str = 'afspm.io.control.control_client.ControlClient'
    control_client_class = parser._import_from_string(control_client_str)
    instance = control_client_class(url=sample_url)
    from afspm.io.control.control_client import ControlClient
    assert isinstance(instance, ControlClient)

    # Lastly, confirm we *cannot* import without the module path.
    no_path_pbc_logic = "ProtoBasedCacheLogic"
    with pytest.raises(ValueError):
        parser._import_from_string(no_path_pbc_logic)


@pytest.fixture
def pbc_logic_str():
    return 'afspm.io.cache.pbc_logic.ProtoBasedCacheLogic'


def test_evaluate_value_str(control_client_str, sample_url,
                            pbc_logic_str):
    """Ensure we handle evaluating values in key:val pairs properly."""
    # Test doing nothing (url)
    res = parser._evaluate_value_str(sample_url)
    assert isinstance(res, str)
    assert sample_url == res

    # Test importing only
    res = parser._evaluate_value_str(control_client_str)
    assert isinstance(res, type)

    # Test instantiated method
    res = parser._evaluate_value_str(pbc_logic_str + '()')
    from afspm.io.cache.pbc_logic import ProtoBasedCacheLogic
    assert isinstance(res, ProtoBasedCacheLogic)


@pytest.fixture
def afspm_component_params_dict():
    cache_kwargs = {"cache_logic":
                    'afspm.io.cache.pbc_logic.ProtoBasedCacheLogic()'}
    return {
        'class': 'afspm.components.afspm_component.AfspmComponent',
        'name': 'BananaHammock',
        'loop_sleep_s': 0,
        'hb_period_s': 5,
        'subscriber': {
            'class': 'afspm.io.pubsub.subscriber.Subscriber',
            'sub_url': 'tcp://127.0.0.1:5555',
            'sub_extract_proto':
            'afspm.io.cache.cache_logic.extract_proto',
            'topics_to_sub': [],
            'update_cache':
            'afspm.io.cache.cache_logic.update_cache',
            'extract_proto_kwargs': cache_kwargs,
            'update_cache_kwargs': cache_kwargs
        }
    }


def test_construct_component(afspm_component_params_dict):
    """Confirm we can construct a component from a dict.

    This tests _construct_component() *without* testing the run()
    part.
    """
    afspm_component_params_dict['ctx'] = zmq.Context.instance()
    res = parser._construct_component(afspm_component_params_dict)
    from afspm.components.afspm_component import AfspmComponent
    assert isinstance(res, AfspmComponent)
