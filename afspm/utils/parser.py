"""Config file parsing (and populating) methods.

The two main methods here are:
- expand_variables_in_dict()
- construct_and_run_component().

All other methods are used within these and thus private.
"""

import sys
import os
import copy
import logging
from importlib import import_module
from typing import Any, Callable


logger = logging.getLogger(__name__)


# Constants for string evaluation of inspection/instantiation.
INSPECTABLE_CHAR = '.'
URL_CHAR = ':'
INST_CHARS = '()'
ARG_SEP = ','

# Constant expected key indicating something is a class and should be
# instantiated
CLASS_KEY = 'class'

# Key to indicate that the dictionary is a component.
IS_COMPONENT_KEY = 'component'

# Substring of key indicating the item is a url
IS_URL_KEY = 'url'


def expand_variables_in_dict(config_dict: dict) -> dict:
    """Replace any 'variable' values in a dict with their values.

    Given a dictionary of key:val pairs where some values correspond to other
    keys, this method will replace all variables with their value (in effect
    'expanding' out the variables).

    E.g.: Given:
        pub_url: 'tcp://127.0.0.1:5555'
        publisher:
            url: 'pub_url'
            get_envelope_given_proto: 'afspm.io.pubsub.logic.cache_logic.
                CacheLogic.get_envelope_for_proto'
    , it will expand out into:
        pub_url: 'tcp://127.0.0.1:5555'
        publisher:
            url: 'tcp://127.0.0.1:5555'
            get_envelope_for_proto: 'afspm.io.pubsub.logic.cache_logic.
                CacheLogic.get_envelope_for_proto'

    Args:
        config_dict: dictionary to expand variables in.

    Returns:
        dictionary, with variables expanded out.
    """
    return _expand_variables_recursively(config_dict, None)


def _expand_variables_recursively(config_dict: dict, sub_dict: dict) -> dict:
    """Recursively go through config_dict, expanding variables out.

    This method will recurse through a dictionary, comparing the values of
    key:val pairs *at any level* with top-level keys. If a match is found
    (implying a top-level variable), it will replace the value of that
    key:val pair with the top-level key's value. See expand_variables_in_dict
    for an example.

    Args:
        config_dict: top-level dictionary we are fixing.
        sub_dict: the dictionary level we are currently expanding out.

    Returns:
        the expanded out version of the sub_dict.
    """
    if sub_dict is None:
        # TODO: Consider live replacement logic (skipping deepcopy)
        config_dict = copy.deepcopy(config_dict)
        sub_dict = config_dict

    for key in sub_dict:
        try:
            if isinstance(sub_dict[key], dict):  # Go deeper in 'tree'
                logger.trace(f"Going into dict for expansion, with key {key}")
                sub_dict[key] = _expand_variables_recursively(
                    config_dict, sub_dict[key])
            elif isinstance(sub_dict[key], list):
                logger.trace(f"Going into list for expansion, with key {key}")
                sub_dict[key] = _expand_variables_list_recursively(
                    config_dict, sub_dict[key])
            elif (isinstance(sub_dict[key], str) and
                  sub_dict[key] in config_dict):  # Expand variable
                logger.debug(f"Expanding {sub_dict[key]} into "
                             f"{config_dict[sub_dict[key]]}.")
                sub_dict[key] = config_dict[sub_dict[key]]
            else:  # Copy value over (this is not a variable)
                logger.trace(f"Keeping {sub_dict[key]} for key {key}.")
                sub_dict[key] = sub_dict[key]
        except Exception:
            logger.error(f"Exception for key:val = {key} : {sub_dict[key]}",
                         exc_info=True)
    return sub_dict


def _expand_variables_list_recursively(config_dict: dict, in_list: list
                                       ) -> list:
    """Recursively go through a list, expanding variables out.

    This method is a list-specific expansion of _expand_variables_recursively,
    used within it to expand lists specifically. Read that method for the
    explanation.
    """
    for idx in range(len(in_list)):
        if isinstance(in_list[idx], dict):  # Go deeper
            logger.trace(f"Going into dict for expansion with index {idx}")
            in_list[idx] = _expand_variables_recursively(config_dict,
                                                         in_list[idx])
        elif isinstance(in_list[idx], list):  # Go deeper
            logger.trace(f"Going into list for expansion with index {idx}")
            in_list[idx] = _expand_variables_list_recursively(config_dict,
                                                              in_list[idx])
        elif (isinstance(in_list[idx], str) and
              in_list[idx] in config_dict):  # Expand variable
            logger.debug(f"Expanding {in_list[idx]} into "
                         f"{config_dict[in_list[idx]]}.")
            in_list[idx] = config_dict[in_list[idx]]

    return in_list


def construct_and_run_component(params_dict: dict,
                                log_init_method: Callable = None,
                                log_init_args: tuple = None):
    """Build and run an AfspmComponent, for use with multiprocess.

    This method functions as the process method fed to each mp.Process
    constructor, to start up a component and run it.

    Given a dictionary of parameters necessary to construct a component,
    we proceed via the following steps:
    1. We recursively go through the dictionary, evaluating keys so as to
    import and (possibly) instantiate any necessary objects/methods. At this
    point, we will actually have a kwargs dict.
    2. Construct our AfspmComponent with this kwargs dict.

    We leave importing/instantiation in (1) until this method to ensure all
    such memory usage is limited to the process spawned.

    Thus, our params_dict may look like, for example:
    {
        class: 'afspm.components.component.AfspmComponent',
        loop_sleep_s: 0,
        beat_period_s: 5,
        subscriber: {
            class: 'afspm.io.pubsub.subscriber.Subscriber',
            sub_url: 'tcp://127.0.0.1:5555'
            sub_extract_proto:
            'afspm.io.pubsub.logic.cache_logic.extract_proto',
            [...]
        }
    }

    Step (1) will:
    a) Convert params_dict['subscriber']['sub_extract_proto'] from a string to
    a Callable by evaluating the string and importing it.
    b) Convert params_dict['subscriber']['class'] from a string to a 'type'
    instance, so it can be constructed later.
    c) Convert params_dict['subscriber'] to a Subscriber instance by
    calling params_dict['subscriber']['class'](params_dict['subscriber']),
    i.e. using the other values in params_dict['subscriber'] as the kwargs to
    the constructor.

    With this done, we can instantiate our actual AfspmComponent instance using
    a kwargs dict consisting of the other values in this params_dict.

    Note: we also receive an optional log_init_method (and input args), to
    initialize the logger of this new component.

    Args:
        params_dict: dictionary of parameters to feed the
            AfspmComponent's constructor.
        log_init_method: optional method to run, to set up logging parameters.
        log_init_args: optional arguments to pass to log_init_method.
    """
    if log_init_method is not None and log_init_args is not None:
        log_init_method(*log_init_args)

    try:
        component = _construct_component(params_dict)
        component.run()
    except Exception:
        logger.error('Exception running component. Exiting.', exc_info=True)


def _construct_component(params_dict: dict) -> Any:
    """Build a component from a dict of parameters.

    See construct_and_run_component() for full documentation.
    """
    assert CLASS_KEY in params_dict
    evaluated_dict = _evaluate_values_recursively(params_dict)
    return _instantiate_classes_recursively(evaluated_dict)


def _evaluate_values_recursively(params_dict: dict) -> dict:
    """Recursively run _evaluate_value_str() on a dictionary.

    This method will recursively apply _evaluate_value_str() on all values
    in a dictionary, in order to import and (potentially) instantiate any
    methods/objects.

    With, for example:
    {
        class: 'afspm.components.component.AfspmComponent',
        loop_sleep_s: 0,
        beat_period_s: 5,
        subscriber: {
            class: 'afspm.io.pubsub.subscriber.Subscriber',
            sub_url: 'tcp://127.0.0.1:5555'
            sub_extract_proto:
            'afspm.io.pubsub.logic.cache_logic.CacheLogic.extract_proto',
            [...]
        }
    }
    This method will:
    1. Convert params_dict['subscriber']['sub_extract_proto'] from a string to
    a Callable by evaluating the string and importing it.
    2. Convert params_dict['subscriber']['class'] from a string to a 'type'
    instance, so it can be constructed later.

    Args:
        params_dict: the dictionary of key:val pairs to recursively evaluate.

    Returns:
        the expanded out version of the sub_dict.
    """
    kwargs_dict = {}
    for key in params_dict:
        if isinstance(params_dict[key], dict):  # Go deeper in 'tree'
            logger.trace(f"Going into dict for evaluation, with key {key}")
            kwargs_dict[key] = _evaluate_values_recursively(params_dict[key])
        elif isinstance(params_dict[key], list):
            logger.trace(f"Going into list for evaluation, with key {key}")
            kwargs_dict[key] = _evaluate_values_list_recursively(
                params_dict[key])
        elif isinstance(params_dict[key], str):
            logger.debug(f"Evaluating value {params_dict[key]}")
            kwargs_dict[key] = _evaluate_value_str(params_dict[key])
        else:
            logger.trace(f"Keeping key {key} without evaluating")
            kwargs_dict[key] = params_dict[key]
    return kwargs_dict


def _evaluate_values_list_recursively(values_list: list) -> list:
    """Recursively go through values in list, evaluating dicts.

    This is an expansion of _evaluate_values_recursively(), specific to lists.
    Read the description of that method for more info.
    """
    for idx, val in enumerate(values_list):
        if isinstance(val, list):  # Go deeper
            logger.trace(f"Going into list for evaluation, with index {idx}")
            values_list[idx] = _evaluate_values_list_recursively(val)
        elif isinstance(val, dict):  # Evaluate dict
            logger.trace(f"Going into dict for evaluation, with index {idx}")
            values_list[idx] = _evaluate_values_recursively(val)
        elif isinstance(val, str):
            logger.debug(f"Evaluating value {val}")
            values_list[idx] = _evaluate_value_str(val)
    return values_list


def _instantiate_classes_recursively(params_dict: dict) -> Any | dict:
    """Instantiate classes recursively within a provided dict.

    This method will recursively search through a dictionary, finding
    dictionaries containing a 'class' key and instantiating them using that
    dictionary as the constructor's kwargs.

    Args:
        params_dict: the dictionary of key:val pairs to recursively instantiate
            from.

    Returns:
        Either:
        - The input dictionary, with all values handled, or
        - An instantiated class.
    """
    final_dict = {}
    for key in params_dict:
        if isinstance(params_dict[key], dict):  # Go deeper in 'tree'
            logger.trace(f"Going into dict for instantation, with key {key}")
            final_dict[key] = _instantiate_classes_recursively(
                params_dict[key])
        elif isinstance(params_dict[key], list):
            logger.trace(f"Going into list for instantation, with key {key}")
            final_dict[key] = _instantiate_classes_in_list_recursively(
                params_dict[key])
        else:  # Copy over value
            logger.trace(f"Copying over key {key} without changing.")
            final_dict[key] = params_dict[key]

    if CLASS_KEY in final_dict:
        # This is a class level, instantiate a class and return it
        class_obj = final_dict[CLASS_KEY]
        del final_dict[CLASS_KEY]
        logger.debug(f"Instantiating {class_obj} with kwargs: {final_dict}")
        tmp = class_obj(**final_dict)
        return tmp
    return final_dict  # Go up a level


def _instantiate_classes_in_list_recursively(values_list: list) -> list:
    """Recursively go through values in list, instantiating dicts.

    Expansion of _instantiate_classes_recursively(), specific to lists. Read
    that method's description for more info.
    """
    for idx, val in enumerate(values_list):
        if isinstance(val, list):  # Go deeper
            logger.trace(f"Going into list for instantation, with idx {idx}")
            values_list[idx] = _instantiate_classes_in_list_recursively(val)
        elif isinstance(val, dict):  # Instantiate dict
            logger.trace(f"Going into dict for instantation, with idx {idx}")
            values_list[idx] = _instantiate_classes_recursively(val)
    return values_list


def _evaluate_value_str(value: str) -> Any:
    """Evaluate the value string of a key:val pair in an object's kwargs.

    The input value corresponds to the value in a key:val pair of an object's
    kwargs_dict. Before passing it to the constructor, we must ensure that any
    objects have properly been obtained/instantiated. Thus, we evaluate this
    provided string, to see if it is something we need to inspect, and
    (potentially) instantiate.

    Args:
        value: string from key:val pair, which may need to be inspected and
            (maybe) instantiated.

    Returns:
        Evaluated value. It will either be:
        - the same string, if it is not something we needed to inspect/
            instantiate.
        - a callable, corresponding to a method or constructor (i.e. we
            inspected it).
        - an object, i.e. we inspected and evaluated it (instantiating it)
    """
    if INSPECTABLE_CHAR in value and URL_CHAR not in value:
        # This is something we need to inspect!
        args = None
        if INST_CHARS[-1] in value:
            # We have something to instantiate. We will first extract all
            # arguments and evaluate them recursively.
            start_idx = value.find(INST_CHARS[0])
            end_idx = value.rfind(INST_CHARS[1])
            if start_idx > 0 and end_idx > 0:
                arg_strs = value[start_idx:end_idx + 1]
                value = value.replace(arg_strs, '')
                # Remove ends (parentheses); split by ARG_SEP
                arg_strs = arg_strs[1:-1].split(ARG_SEP)
                args = [_evaluate_value_str(arg_str) for arg_str in arg_strs]

        imported = import_from_string(value)
        if args:
            # If there were no arguments, args will be a list with one empty
            # string
            logger.debug(f"Instantiating {value} with args: {args}")
            instantiated = imported(*args) if args != [''] else imported()
            return instantiated  # Return instantiation of what we imported
        logger.debug(f"Imported class/method {imported}")
        return imported  # Return imported class or method
    return value  # Return original string


def import_from_string(obj_path: str) -> Any:
    """Import a class or method given a string like 'a.b.c'.

    This method will import:
    - the exemplary class c, imported from a.b.
    - a method d, imported from a.b (a.b.d).
    - a static method d, imported from class c in package a.b (a.b.c.d).

    To account for the 3rd case, we also try to import the 'sub-mod' path
    'a.b' when extracting a string 'a.b.c.d'.

    Note: modified from Pat's answer here:
    https://stackoverflow.com/questions/452969/does-python-have-an-equivalent
    -to-java-class-forname

    Args:
        obj_path: string describing the class in the form 'a.b.c', where
            'c' is the class name, and 'a.b' is the module path (or one of the
            other supported formats mentioned above).

    Returns:
        The imported object.

    Raises:
        ModuleNotFoundError if the associated module could not be found.
    """
    top_mod_path, _, top_obj = obj_path.rpartition('.')
    sub_mod_path, _, sub_obj = top_mod_path.rpartition('.')

    caught_exc = None
    final_obj = None
    for (module_path, obj_name) in zip([top_mod_path, sub_mod_path],
                                       [top_obj, sub_obj]):
        try:
            if module_path:  # Do not continue if module path is empty
                mod = import_module(module_path)
                final_obj = getattr(mod, obj_name)
                if obj_name == sub_obj:
                    final_obj = getattr(final_obj, top_obj)
                break
        except ModuleNotFoundError as exc:
            logger.trace("Received ModuleNotFoundError. Assuming due to the "
                         "requested module not existing. However, it could "
                         "be an import error *within* the requested module. "
                         "We will print the exception in case.", exc_info=True)
            caught_exc = exc
            continue

    if final_obj:
        return final_obj
    else:
        logger.error(f"Could not import {obj_path}, got exception "
                     f"{caught_exc}.")
        logger.error(caught_exc)
        raise caught_exc


def consider_config_path(config_file: str):
    """Add config path to PATH, so we can load modules locally from it.

    This allows the user to provide relative paths for their experiment that
    are relative to the config file.

    Args:
        config_file: path to TOML config file.
    """
    path = os.path.dirname(os.path.abspath(config_file)) + os.sep
    logger.trace(f'Adding {path} to executing PATH, so we can find it.')
    sys.path.append(path)
