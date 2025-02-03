"""Test graphify tool."""

import logging

import pytest
from graphviz import Graph
from afspm.utils import parser, graphify


logger = logging.getLogger(__name__)


@pytest.fixture
def simple_config_dict():
    return {
        'pub_url': 'tcp://127.0.0.1:5555',
        'my_pub': {
            'class': 'afspm.io.pubsub.publisher.Publisher',
            'url': 'pub_url',
            'get_envelope_given_proto':
            'afspm.io.pubsub.logic.cache_logic.CacheLogic.get_envelope_for_proto'
        },
        'my_sub': {
            'class': 'afspm.io.pubsub.subscriber.Subscriber',
            'url': 'pub_url'
        },
        'translator': {
            'component': True,
            'class': 'some_fancy_package.MyTranslator',
            'publisher': 'my_pub'
        },
        'experiment': {
            'component': True,
            'class': 'some_fancy_package.MyExperiment',
            'subscriber': 'my_sub'
        }
    }


@pytest.fixture
def simple_config_gv():
    return r"""graph config_dict {
    subgraph cluster_url {
        node [color=white style=filled]
        color="/greys3/1" style=filled
        label=urls
        tcp__127_0_0_1__5555 [label="tcp://127.0.0.1:5555" color="/set19/1"]
    }
    subgraph cluster_translator {
        node [color=white style=filled]
        edge [penwidth=2]
        color="/blues3/1" style=filled
        label="translator\n[MyTranslator]"
        translator_publisher [label="publisher\n[Publisher]"]
        translator_publisher -- tcp__127_0_0_1__5555 [color="/set19/1"]
    }
    subgraph cluster_experiment {
        node [color=white style=filled]
        edge [penwidth=2]
        color="/blues3/1" style=filled
        label="experiment\n[MyExperiment]"
        experiment_subscriber [label="subscriber\n[Subscriber]"]
        experiment_subscriber -- tcp__127_0_0_1__5555 [color="/set19/1"]
    }
}
"""


@pytest.fixture
def combo_config_dict():
    return {
        'pub_url': 'tcp://127.0.0.1:5555',
        'new_info_url': 'tcp://127.0.0.1:5556',
        'my_pub': {
            'class': 'afspm.io.pubsub.publisher.Publisher',
            'url': 'pub_url',
            'get_envelope_given_proto':
            'afspm.io.pubsub.logic.cache_logic.CacheLogic.get_envelope_for_proto'
        },
        'my_sub': {
            'class': 'afspm.io.pubsub.subscriber.Subscriber',
            'url': 'pub_url'
        },
        'new_info_pub': {
            'class': 'afspm.io.pubsub.publisher.Publisher',
            'url': 'new_info_url'
        },
        'new_info_sub': {
            'class': 'afspm.io.pubsub.subscriber.Subscriber',
            'url': 'new_info_url'
        },
        'combo_sub': {
            'class': 'afspm.io.pubsub.subscriber.ComboSubscriber',
            'subs': ['sub_spm', 'sub_points']
        },
        'translator': {
            'component': True,
            'class': 'some_fancy_package.MyTranslator',
            'publisher': 'my_pub'
        },
        'evaluator': {
            'component': True,
            'class': 'some_fancy_package.MyEvaluator',
            'subscriber': 'my_pub',
            'publisher': 'new_info_pub'
        },
        'experiment': {
            'component': True,
            'class': 'some_fancy_package.MyExperiment',
            'subscriber': 'combo_sub'
        }
    }


@pytest.fixture
def combo_config_gv():
    return r"""graph config_dict {
    subgraph cluster_url {
        node [color=white style=filled]
        color="/greys3/1" style=filled
        label=urls
        tcp__127_0_0_1__5555 [label="tcp://127.0.0.1:5555" color="/set19/1"]
        tcp__127_0_0_1__5556 [label="tcp://127.0.0.1:5556" color="/set19/2"]
    }
    subgraph cluster_translator {
        node [color=white style=filled]
        edge [penwidth=2]
        color="/blues3/1" style=filled
        label="translator\n[MyTranslator]"
        translator_publisher [label="publisher\n[Publisher]"]
        translator_publisher -- tcp__127_0_0_1__5555 [color="/set19/1"]
    }
    subgraph cluster_evaluator {
        node [color=white style=filled]
        edge [penwidth=2]
        color="/blues3/1" style=filled
        label="evaluator\n[MyEvaluator]"
        evaluator_subscriber [label="subscriber\n[Publisher]"]
        evaluator_subscriber -- tcp__127_0_0_1__5555 [color="/set19/1"]
        evaluator_publisher [label="publisher\n[Publisher]"]
        evaluator_publisher -- tcp__127_0_0_1__5556 [color="/set19/2"]
    }
    subgraph cluster_experiment {
        node [color=white style=filled]
        edge [penwidth=2]
        color="/blues3/1" style=filled
        label="experiment\n[MyExperiment]"
    }
}
"""


def _get_gv_from_dict(config_dict: dict) -> str:
    logger.debug('Expand dict (easier writing of dicts).')
    expanded_dict = parser.expand_variables_in_dict(config_dict)
    logger.debug('Create gv...')
    gv_str = graphify._convert_config_to_graph_file(expanded_dict,
                                                    'config_dict').source
    # Convert tabs to spaces
    gv_str = gv_str.replace('\t', '    ')
    return gv_str


def test_graphify_simple(simple_config_dict, simple_config_gv):
    """Validate graphify converts to gv files as expected."""
    gv_str = _get_gv_from_dict(simple_config_dict)
    assert gv_str == simple_config_gv


def test_graphify_combo(combo_config_dict, combo_config_gv):
    """Validate graphify converts to gv files as expected."""
    gv_str = _get_gv_from_dict(combo_config_dict)
    assert gv_str == combo_config_gv
