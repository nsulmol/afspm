"""Top-level module, to spawn afspm components for an experiment."""

import logging
import sys
from types import MappingProxyType  # Immutable dict

import tomli
import fire

# TODO: Figure out why this can't be relative?
from afspm.utils.parser import expand_variables_in_dict
from afspm.components.monitor import AfspmComponentsMonitor


logger = logging.getLogger(__name__)


LOGGER_ROOT = 'afspm'
IS_COMPONENT_KEY = 'component'
MONITOR_KEY = 'afspm_components_monitor'
TRACE_LOG_LEVEL = logging.DEBUG - 5

LOG_LEVEL_STR_TO_VAL = MappingProxyType({
    'NOTSET': logging.NOTSET,
    'TRACE': TRACE_LOG_LEVEL,
    'DEBUG': logging.DEBUG,
    'INFO': logging.INFO,
    'WARNING': logging.WARNING,
    'ERROR': logging.ERROR,
    'CRITICAL': logging.CRITICAL
})


def spawn_components(config_file: str,
                     components_to_spawn: list[str] = None,
                     log_file: str = 'log.txt',
                     log_to_stdout: bool = True,
                     log_level: str = "INFO"):
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
    class name rather than the instance name: afspm_components_monitor
    (note switch from CamelCase to snake_case).

    This is because the monitor is what spawns the other components. For this
    exception, we expect the following in the TOML:
        [afspm_components_monitor]
        loop_sleep_s = ...
        missed_beats_before_dead = ...

    Once spawned, the components monitor's run method is called. This is
    a blocking call.

    Notes:
    - $COMPONENT_CLASS$ must be: the module_path + class name:
    'path.to.module.class' (e.g.
    'afspm.components.device.controller.DeviceController').
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
        log_file: a file path to save the process log. Default is 'log.txt'.
            To not log to file, set to None.
        log_to_stdout: whether or not we print to std out as well. Default is
            True.
        log_level: the log level to use. Default is INFO.
    """
    _set_up_logging(log_file, log_to_stdout, log_level)

    monitor = None
    with open(config_file, 'rb') as file:
        config_dict = tomli.load(file)

        expanded_dict = expand_variables_in_dict(config_dict)
        filtered_dict = _filter_requested_components(expanded_dict,
                                                     components_to_spawn)
        if MONITOR_KEY in expanded_dict:
            monitor = AfspmComponentsMonitor(filtered_dict,
                                             **expanded_dict[MONITOR_KEY])
        else:
            monitor = AfspmComponentsMonitor(filtered_dict)

    if monitor:
        monitor.run()


def _set_up_logging(log_file: str, log_to_stdout: bool, log_level: str):
    """Set up logging logic.

    Args:
        log_file: a file path to save the process log. Default is 'log.txt'.
            To not log to file, set to None.
        log_to_std_out: whether or not we print to std out as well. Default is
            True.
        log_level: the log level to use. Default is INFO.
    """
    root = logging.getLogger(LOGGER_ROOT)
    log_level = LOG_LEVEL_STR_TO_VAL[log_level.upper()]
    root.setLevel(log_level)
    formatter = logging.Formatter('%(asctime)s.%(msecs)03d- %(name)s - '
                                  '%(levelname)s:%(lineno)s - %(message)s')

    handlers = []
    if log_file:
        handlers.append(logging.FileHandler(log_file))
    if log_to_stdout:
        handlers.append(logging.StreamHandler(sys.stdout))

    for handler in handlers:
        handler.setLevel(log_level)
        handler.setFormatter(formatter)
        root.addHandler(handler)


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

    Note: we also copy the parent key (which is the name) into a 'name' key in
    the sub-dict. This is for convenience elsewhere, where we use the 'name'
    key to determine the component's name.

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
        if (isinstance(config_dict[key], dict) and
                (components_to_spawn is None or key in components_to_spawn)):
            if IS_COMPONENT_KEY in config_dict[key]:
                config_dict[key].pop(IS_COMPONENT_KEY, None)
                config_dict[key]['name'] = key
                filtered_dict[key] = config_dict[key]
            elif components_to_spawn is not None:
                msg = ("Requested component %s, but this is not a "
                       "component (does not have 'component': True "
                       "key:val pair)!" % key)
                logger.error(msg)
                raise KeyError(msg)
    return filtered_dict


def cli():
    fire.Fire(spawn_components)


if __name__ == '__main__':
    cli()
