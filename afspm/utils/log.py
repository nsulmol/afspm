"""Logging methods/helpers."""

import logging
from colorlog import ColoredFormatter
import sys
import zmq
from types import MappingProxyType  # Immutable dict


LOGGER_ROOT = 'afspm'
TRACE_LOG_LEVEL = logging.DEBUG - 5

LOG_LEVEL_STR_TO_INT = MappingProxyType({
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

    Taken directly from: https://stackoerflow.com/a/35804945.

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


def set_up_logging(log_file: str = None, log_to_stdout: bool = True,
                   log_level: str | int = logging.INFO,
                   log_url: str = None, ctx: zmq.Context = None):
    """Set up logging logic.

    Args:
        log_file: a file path to save the process log. Default is None.
        log_to_std_out: whether or not we print to std out as well. Default is
            True.
        log_level: the log level to use, as a string or int. Default is INFO.
        log_url: if provided, a PUBHandler is used to route logs to the
            provided zmq url.
        ctx: context linked to zmq url, for creating the socket.
    """
    root = logging.getLogger(LOGGER_ROOT)

    if root.hasHandlers():  # Delete existing handlers before adding ours
        root.handlers.clear()

    if isinstance(log_level, str):
        log_level = LOG_LEVEL_STR_TO_INT[log_level.upper()]

    root.setLevel(log_level)
    formatter = logging.Formatter(
        '%(asctime)s | %(name)s | '
        '%(levelname)s:%(lineno)s | '
        '%(message)s')

    color_formatter = ColoredFormatter(
        '%(asctime)s | %(name)s | '
        '%(log_color)s%(levelname)s%(reset)s:%(lineno)s | '
        '%(log_color)s%(message)s%(reset)s')

    handlers = []
    if log_file:
        handler = logging.FileHandler(log_file)
        handler.setFormatter(formatter)
        handlers.append(handler)
    if log_to_stdout:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(color_formatter)
        handlers.append(handler)
    if log_url and ctx:
        pub = zmq.socket(zmq.PUB, ctx)
        pub.connect(log_url)
        handler = zmq.log.handlers.PUBHandler(pub)
        handler.setFormatter(formatter)
        handlers.append(handler)
    for handler in handlers:
        handler.setLevel(log_level)
        root.addHandler(handler)
