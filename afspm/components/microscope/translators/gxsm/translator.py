"""Handles device communication with the gxsm3 controller."""

import os.path
import logging

from afspm.components.microscope.params import ParameterHandler
from afspm.components.microscope.actions import (ActionHandler,
                                                 CallableActionHandler)
from afspm.components.microscope.translator import (
    get_file_modification_datetime)
from afspm.components.microscope.config_translator import ConfigTranslator
from afspm.utils import array_converters as conv
from afspm.io.protos.generated import scan_pb2
from afspm.io.protos.generated import signal_pb2

import gxsm  # Dynamic DLL, so not in pyproject.
from gxsmread import read

from . import params


logger = logging.getLogger(__name__)


# Attributes from the read scan file (differs from params.toml, which
# contains UUIDs for getting/setting parameters).
SCAN_ATTRIB_ANGLE = 'alpha'

# Default filenames for actions and params config files.
ACTIONS_FILENAME = 'actions.toml'
PARAMS_FILENAME = 'params.toml'


class GxsmTranslator(ConfigTranslator):
    """Handles device communication with the gxsm3 controller.

    Note that GXSM does not currently feed the various physical units via
    the API. This translator uses the defined units in its params.toml file
    to perform conversions. Therefore it is *IMPORTANT* that you ensure the
    gxsm UI units match the params.toml ones!

    For units, these can be found/set in Preferences->User->User/XYUnit,
    for physical XY units, for example.
    """

    STATE_RUNNING_THRESH = 0
    MOTOR_RUNNING_THRESH = -2

    MAX_NUM_CHANNELS = 6

    # This error is sent if you request a channel's filename but provide an
    # invalid channel id. Thus, if iterating through the channels of a scan,
    # this will be provided when our index is too big (we've gone through all
    # the channels.
    CHANNEL_FILENAME_ERROR_STR = 'EE: invalid channel'

    def __init__(self,
                 read_channels_config_path: str = None,
                 read_use_physical_units: bool = True,
                 read_allow_convert_from_metadata: bool = False,
                 read_simplify_metadata: bool = True,
                 param_handler: ParameterHandler = None,
                 action_handler: ActionHandler = None,
                 zctrl_channel: str = None,
                 **kwargs):
        """Initialize internal logic."""
        self.read_channels_config_path = read_channels_config_path
        self.read_use_physical_units = read_use_physical_units
        self.read_allow_convert_from_metadata = read_allow_convert_from_metadata
        self.read_simplify_metadata = read_simplify_metadata

        self.last_scan_fname = ''
        self.old_scans = []

        # Default initialization of handlers and addition to kwargs
        if not action_handler:
            action_handler = _init_action_handler()
            kwargs['action_handler'] = action_handler
        if not param_handler:
            param_handler = _init_param_handler()
            if zctrl_channel:  # Update Z-Ctrl feedback channel
                zctrl_enum = params.GxsmFeedbackChannel(zctrl_channel)
                params.update_zctrl_channel(param_handler, zctrl_enum)
            kwargs['param_handler'] = param_handler
        super().__init__(**kwargs)

    def poll_scope_state(self) -> scan_pb2.ScopeState:
        """Return current scope state in accordance with system model."""
        # Note: updating self.scope_state is handled by the calling method
        # in MicroscopeTranslator.
        state = get_current_scope_state(self.param_handler)
        if (self.scope_state == scan_pb2.ScopeState.SS_SCANNING and
                state == scan_pb2.ScopeState.SS_FREE):
            gxsm.autosave()  # Save the images we have recorded
        return state

    def poll_scans(self) -> [scan_pb2.Scan2d]:
        """Override scans polling."""
        channel_idx = 0
        fnames = []
        last_scan_fname = None

        try:
            while channel_idx < self.MAX_NUM_CHANNELS:
                fname = gxsm.chfname(channel_idx)

                # Avoid reloading scans if the same. Return old scans.
                if channel_idx == 0:
                    if fname == self.last_scan_fname:
                        return self.old_scans
                    last_scan_fname = fname

                # Break if on last 'set' channel
                if fname == self.CHANNEL_FILENAME_ERROR_STR:
                    break

                # Only append actually saved files
                if os.path.isfile(fname):
                    fnames.append(fname)
                channel_idx += 1
        except Exception as exc:
            logger.trace(f"Exception with requesting channel {channel_idx}: "
                         f"{str(exc)}")

        # Avoid reloading scans if they are not new.
        if len(fnames) > 0:
            scans = []
            for fname in fnames:
                ts = get_file_modification_datetime(fname)
                try:
                    ds = read.open_dataset(
                        fname, self.read_channels_config_path,
                        self.read_use_physical_units,
                        self.read_allow_convert_from_metadata,
                        self.read_simplify_metadata,
                        engine='scipy')
                except Exception as exc:
                    logger.error(f"Could not read scan fname {fname}, "
                                 f"got error {exc}.")
                    continue

                # Grabbing first data variable, since each channel is
                # stored in its own file (so each file should have only
                # one data variable).
                scan = conv.convert_xarray_to_scan_pb2(
                    ds[list(ds.data_vars)[0]])

                # Set ROI angle, timestamp, filename
                scan.params.spatial.roi.angle = ds.attrs[SCAN_ATTRIB_ANGLE]
                scan.timestamp.FromDatetime(ts)
                scan.filename = fname

                scans.append(scan)
            self.last_scan_fname = last_scan_fname
            self.old_scans = scans
        return self.old_scans

    def poll_signal(self) -> signal_pb2.Signal1d:
        """Override signal polling. For now, not supported."""
        return signal_pb2.Signal1d()


def get_current_scope_state(param_handler: ParameterHandler
                            ) -> scan_pb2.ScopeState:
    """Return the current scope state.

    This queries gxsm for its current scope state.

    Returns:
        ScopeState, or None if query fails.
    """
    svec = gxsm.rtquery('s')  # presumably s for state
    s = int(svec[0])
    # (2+4) == Scanning; 8 == Vector Probe
    scanning = (s & (2+4) > GxsmTranslator.STATE_RUNNING_THRESH or
                s & 8 > GxsmTranslator.STATE_RUNNING_THRESH)
    moving = s & 16 > GxsmTranslator.STATE_RUNNING_THRESH

    # TODO: investigate motor logic further...
    motor_running = (param_handler.get_param_spm(params.MOTOR_PARAM) <
                     GxsmTranslator.MOTOR_RUNNING_THRESH)
    if motor_running:
        return scan_pb2.ScopeState.SS_COARSE_MOTOR
    if scanning:
        return scan_pb2.ScopeState.SS_SCANNING
    if moving:
        return scan_pb2.ScopeState.SS_MOVING
    return scan_pb2.ScopeState.SS_FREE


def _init_action_handler() -> ActionHandler:
    """Initialize GXSM action handler pointing to defulat config."""
    actions_config_path = os.path.join(os.path.dirname(__file__),
                                       ACTIONS_FILENAME)
    return CallableActionHandler(actions_config_path)


def _init_param_handler() -> params.GxsmParameterHandler:
    """Initialize GXSM action handler pointing to defulat config."""
    params_config_path = os.path.join(os.path.dirname(__file__),
                                      PARAMS_FILENAME)
    return params.GxsmParameterHandler(params_config_path)
