"""Top-level module, to spawn afspm components for an experiment."""

import logging
import sys
from types import MappingProxyType  # Immutable dict

import toml
import fire

from .utils.parser import expand_variables_in_dict
from .components.afspm_components_monitor import AfspmComponentsMonitor


logger = logging.getLogger(__name__)


IS_COMPONENT_KEY = 'component'
MONITOR_KEY = 'AfspmComponentsMonitor'
MONITOR_LOOP_SLEEP_KEY = 'loop_sleep_s'
MONITOR_BEATS_BEFORE_DEAD_KEY = 'missed_beats_before_dead'

LOG_LEVEL_STR_TO_VAL = MappingProxyType({
    'NOTSET': logging.NOTSET,
    'DEBUG': logging.DEBUG,
    'INFO': logging.INFO,
    'WARNING': logging.WARNING,
    'ERROR': logging.ERROR,
    'CRITICAL': logging.CRITICAL
})


def spawn_components(config_file: str,
                     components_to_spawn: list[str] = None,
                     encoding: str = 'utf-8',
                     log_file: str = 'log.txt',
                     log_to_stdout: bool = True,
                     log_level_str: str = ""):
    """Spawn afspm components from a provided config file.

    This method takes as input a TOML file of the following structure:
        $URL_NAME1$ = ...
        ...
        [$IO_NAME1$]
        class = $IO_CLASS1$
        url = $URL_NAME_1$
        $PARAM2$ = ...
        ...
        [$COMPONENT_NAME$]
        class = $COMPONENT_CLASS1$
        $PARAM1$ = $IO_NAME1$
        ...
        [$COMPONENT2_NAME$]
        class = $COMPONENT_CLASS2$
        ...
    where
    - $URL_NAME1$ is a key for a variable, e.g. to be used as an
        argument for an I/O class (like a subscriber.Subscriber).
    - $IO_NAME1$ is the *instance* name of an I/O class we want to create.
    - $IO_CLASS1$ is the class name for that instance we wish to spawn.
    - $COMPONENT_NAME$ is the *instance* name of an AfspmComponent to spawn.
    - $COMPONENT_CLASS$ is the class name for that instance we wish to spawn.
    - $PARAM_1$-$PARAM_X$ are the parameters to be fed to the constructor to
    spawn the class (be that a component or I/O class).

    It proceeds to spawn the components in components_to_spawn, where the
    strings provided are the *instance* names of each component desired.

    All spawned components are monitored via a single AfspmComponentsMonitor
    instance, which restarts any crashed/frozen components.

    There is one 'special case' to the above, where the dict key is the
    class name rather than the instance name: AfspmComponentsMonitor.

    This is because the monitor is what spawns the other components. For this
    exception, we expect the following in the TOML:
        [AfspmComponentsMonitor]
        loop_sleep_s = ...
        missed_beats_before_dead = ...

    Once spawned, the components monitor's run method is called. This is
    a blocking call.

    Notes:
    - $COMPONENT_CLASS$ must be: the module_path + class name:
    'path.to.module.class' (e.g.
    'afspm.components.device_controller.DeviceController').
    - We  expect you to have a *single* experiment config file that contains
    all the components you want to run in your experiment. You may instantiate
    these components on multiple different devices (using different
    components_to_spawn for each device), but there should be one config.

    Args:
        config_file: path to TOML config file.
        components_to_spawn: list of strings, with each string corresponding
            to a $COMPONENT_NAME$ in the config file. If None, all components
            in the config file will be spawned. Note: any 'component' requires
            a key:val of 'component': True for us to parse it properly!
        encoding: encoding to use for reading config file, as a str.
        log_file: a file path to save the process log. Default is 'log.txt'.
            To not log to file, set to None.
        log_to_stdout: whether or not we print to std out as well. Default is
            True.
        log_level_str: the log level to use. Default is INFO.
    """
    _set_up_logging(log_file, log_to_stdout, log_level_str)

    monitor = None
    with open(config_file, 'r', encoding=encoding)as file:
        config_dict = toml.load(file)

        loop_sleep_s, beats_before_dead = _get_monitor_parameters(
            config_dict)
        filtered_dict = _prepare_dict_for_spawning(config_dict,
                                                   components_to_spawn)
        monitor = AfspmComponentsMonitor(filtered_dict, loop_sleep_s,
                                         beats_before_dead)

    if monitor:
        monitor.run()


def _set_up_logging(log_file: str = 'log.txt',
                    log_to_stdout: bool = True,
                    log_level_str: str = ""):
    """Set up logging logic.

    Args:
        log_file: a file path to save the process log. Default is 'log.txt'.
            To not log to file, set to None.
        log_to_std_out: whether or not we print to std out as well. Default is
            True.
        log_level_str: the log level to use. Default is INFO.
    """

    log_level = LOG_LEVEL_STR_TO_VAL[log_level_str.upper()]
    logger.setLevel(log_level)
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - '
                                  '%(message)s')

    handlers = []
    if log_file:
        handlers.append(logging.FileHandler(log_file))
    if log_to_stdout:
        handlers.append(logging.StreamHandler(sys.stdout))

    for handler in handlers:
        handler.setLevel(log_level)
        handler.setFormatter(formatter)
        logger.addHandler(handler)


def _get_monitor_parameters(config_dict) -> (int, int):
    """Validate that we have AfspmMonitorComponents parameters and provide.

    Args:
        config_dict: dictionary to analyze.

    Returns:
        tuple consisting of 'loop_sleep_s' and 'missed_beats_before_end'
        parameters for instantiating the AfspmComponentsMonitor.
    """
    if MONITOR_KEY not in config_dict:
        msg = ("%s key not found in config file, cannot continue." %
               MONITOR_KEY)
        logger.error(msg)
        raise KeyError(msg)
    return (config_dict[MONITOR_KEY][MONITOR_LOOP_SLEEP_KEY],
            config_dict[MONITOR_KEY][MONITOR_BEATS_BEFORE_DEAD_KEY])


def _prepare_dict_for_spawning(config_dict: dict,
                               components_to_spawn: list[str] = None) -> dict:
    """The 'setup' portion of spawn_components().

    Here, we:
    - Expand the variables in our config_dict out.
    - Filter out key:vals from our config_dict that are not components we want
    to spawn.

    Args:
        config_dict: dictionary to analyze.
        components_to_spawn: list of strings, with each string corresponding
            to a $COMPONENT_NAME$ in the config file. If None, all components
            in the config file will be spawned. Note: any 'component' requires
            a key:val of 'component': True for us to parse it properly!

    Returns:
        A new dict consisting only of the key:val pairs associated with the
        components we want to spawn.
    """
    expanded_dict = expand_variables_in_dict(config_dict)
    return _filter_requested_components(expanded_dict, components_to_spawn)


def _filter_requested_components(config_dict: dict,
                                 components_to_spawn: list[str] = None,
                                 ) -> dict:
    """Iterate through config_dict, filtering out requested components only.

    This will return a new dict consisting only of the requested components. It
    confirms each requested component is an 'actual' AfspmComponent via a hack:
    it expects such a dict to contain a key:val 'component': True.

    If the no list of components are provided, we iterate through all keys in
    the config dict and accept all key:vals that contain a 'component': True
    key:val.

    Args:
        config_dict: dictionary to analyze.
        components_to_spawn: list of strings, with each string corresponding
            to a $COMPONENT_NAME$ in the config file. If None, all components
            in the config file will be spawned. Note: any 'component' requires
            a key:val of 'component': True for us to parse it properly!

    Returns:
        A new dict consisting only of the key:val pairs associated with the
            components we want to spawn.
    """
    filtered_dict = {}
    for key in config_dict:
        if components_to_spawn is None or key in components_to_spawn:
            if IS_COMPONENT_KEY in config_dict[key]:
                filtered_dict[key] = config_dict[key]
            elif components_to_spawn is not None:
                msg = ("Requested component %s, but this is not a "
                       "component (does not have 'component': True "
                       "key:val pair)!" % key)
                logger.error(msg)
                raise KeyError(msg)
    return filtered_dict


if __name__ == '__main__':
    fire.Fire(spawn_components)
