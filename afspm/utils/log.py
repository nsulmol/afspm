"""Allows adding new log types to logger package.

Taken directly from: https://stackoerflow.com/a/35804945.
"""

import logging
from colorlog import ColoredFormatter
import sys
from types import MappingProxyType  # Immutable dict


LOGGER_ROOT = 'afspm'
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


def addLoggingLevel(levelName, levelNum, methodName=None):
    """Add a new logging level to our logger, for filtering.

    Comprehensively adds a new logging level to the `logging` module and the
    currently configured logging class.

    `levelName` becomes an attribute of the `logging` module with the value
    `levelNum`. `methodName` becomes a convenience method for both `logging`
    itself and the class returned by `logging.getLoggerClass()` (usually just
    `logging.Logger`). If `methodName` is not specified, `levelName.lower()` is
    used.

    To avoid accidental clobberings of existing attributes, this method will
    raise an `AttributeError` if the level name is already an attribute of the
    `logging` module or if the method name is already present

    Example
    -------
    >>> addLoggingLevel('TRACE', logging.DEBUG - 5)
    >>> logging.getLogger(__name__).setLevel("TRACE")
    >>> logging.getLogger(__name__).trace('that worked')
    >>> logging.trace('so did this')
    >>> logging.TRACE
    """
    if not methodName:
        methodName = levelName.lower()

    if hasattr(logging, levelName):
        raise AttributeError('{} already defined in logging module'.format(
            levelName))
    if hasattr(logging, methodName):
        raise AttributeError('{} already defined in logging module'.format(
            methodName))
    if hasattr(logging.getLoggerClass(), methodName):
        raise AttributeError('{} already defined in logger class'.format(
            methodName))

    # This method was inspired by the answers to Stack Overflow post
    # http://stackoverflow.com/q/2183233/2988730, especially
    # http://stackoverflow.com/a/13638084/2988730
    def logForLevel(self, message, *args, **kwargs):
        if self.isEnabledFor(levelNum):
            self._log(levelNum, message, args, **kwargs)

    def logToRoot(message, *args, **kwargs):
        logging.log(levelNum, message, *args, **kwargs)

    logging.addLevelName(levelNum, levelName)
    setattr(logging, levelName, levelNum)
    setattr(logging.getLoggerClass(), methodName, logForLevel)
    setattr(logging, methodName, logToRoot)


def set_up_logging(log_file: str, log_to_stdout: bool, log_level: str):
    """Set up logging logic.

    Args:
        log_file: a file path to save the process log. Default is 'log.txt'.
            To not log to file, set to None.
        log_to_std_out: whether or not we print to std out as well. Default is
            True.
        log_level: the log level to use. Default is INFO.
    """
    root = logging.getLogger(LOGGER_ROOT)

    if root.hasHandlers():  # Delete existing handlers before adding ours
        root.handlers.clear()

    log_level = LOG_LEVEL_STR_TO_VAL[log_level.upper()]
    root.setLevel(log_level)
    formatter = ColoredFormatter(
        '%(asctime)s | %(name)s | '
        '%(log_color)s%(levelname)s%(reset)s:%(lineno)s | '
        '%(log_color)s%(message)s%(reset)s')

    handlers = []
    if log_file:
        handlers.append(logging.FileHandler(log_file))
    if log_to_stdout:
        handlers.append(logging.StreamHandler(sys.stdout))

    for handler in handlers:
        handler.setLevel(log_level)
        handler.setFormatter(formatter)
        root.addHandler(handler)
