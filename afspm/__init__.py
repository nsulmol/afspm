"""Sets up trace level, so it exists everywhere."""

from .utils import log
from .spawn import TRACE_LOG_LEVEL

# Add 'TRACE' logging level.
log.addLoggingLevel("TRACE", TRACE_LOG_LEVEL)
