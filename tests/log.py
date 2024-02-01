"""Mechanism to ensure logs are routed to stdout in tests.

Since our components are spawned as their own processes (via multiprocessing),
and since we force process 'spawning' to be consistent across OSes, our new
processes will *not* share the logger initialization from the original process.

We work around this in afspm by passing a log_init method to
construct_and_run_component, ensuring that the appropriate initialization is
done in this process.

For pytest, the problem is similar: we want to set up the logger on startup and
when a new process is spawned. To accomplish this, we provide:
- a pytest fixture that records the log-cli-level fed to pytest,
- a helper method that will initialize set_up_logging() when called, and return
a log_init_method, log_init_args tuple which can be fed to the
AfspmComponentsMonitor constructor. This means you only have to import *this*
module.

This logic is only needed for tests where you are spawning processes! Outside
of this, pytest will function well.
==========
Usage
==========

In your test, call this at startup:

log_init_method, log_init_args = setup_and_get_logging_args(log_cli_level)

When calling construct_and_run_component() *or* constructing an
AfspmComponentsMonitor, feed log_init_method and log_init_args as the input
arguments (for the appropriate arguments).

Note: you will need to include log_cli_level as an input fixture to your test.

You will need to call your test with *both* --log-cli-level and -rP, to ensure
the stdout is printed. This is because we have not figured out how to
initialize our process logger with the same handler as is used by pytest.

--log-cli-level will define the log level we use. -rP tells pytest to show all
printed output.

The 'full' log will be in the 'Captured stderr call' output.

Note: another option is to call get_logging_args(), which will allow decoupling
of logs from the spawned process (in stderr) and the original process (in the
pytest log).
"""

import pytest
import logging
from afspm.utils import log


@pytest.fixture
def log_cli_level(pytestconfig):
    log_level = pytestconfig.getoption('log_cli_level', default=None)
    if log_level is None:
        log_level = logging.NOTSET
    return log_level


def setup_and_get_logging_args(log_cli_level):
    """Call set_up_logging() and gets the args to call again in a process."""
    log_init_method = log.set_up_logging
    log_init_args = (None, True, log_cli_level)

    # Call at start, to get as many logs as possible
    log_init_method(*log_init_args)

    return log_init_method, log_init_args


def get_logging_args(log_cli_level):
    """Get args to set up logging in a process.

    Same as above, but will *not* call setup to begin. The purpose is to
    decouple the spawned process log from the main process one.
    """
    log_init_method = log.set_up_logging
    log_init_args = (None, True, log_cli_level)
    return log_init_method, log_init_args
