"""Sets up trace level, so it exists everywhere."""

from .utils import log

# Add 'TRACE' logging level.
log.addLoggingLevel("TRACE", log.TRACE_LOG_LEVEL)


# TODO: Remove when no longer necessary!
# Remove deprecation warning for upcoming PyArrow requirement in pandas,
# which is used by xarray.
import warnings
warnings.filterwarnings("ignore", category=DeprecationWarning)
