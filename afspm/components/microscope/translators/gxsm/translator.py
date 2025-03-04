"""Handles device communication with the gxsm3 controller."""

import os.path
import logging
import glob
import pandas as pd

from afspm.components.microscope.params import ParameterHandler
from afspm.components.microscope.actions import ActionHandler
from afspm.components.microscope.translator import (
    get_file_modification_datetime)
from afspm.components.microscope.config_translator import ConfigTranslator
from afspm.utils import array_converters as conv
from afspm.io.protos.generated import geometry_pb2
from afspm.io.protos.generated import scan_pb2
from afspm.io.protos.generated import spec_pb2

import gxsm  # Dynamic DLL, so not in pyproject.
from gxsmread import read, spec

from . import params
from . import actions


logger = logging.getLogger(__name__)


# Attributes from the read scan file (differs from params.toml, which
# contains UUIDs for getting/setting parameters).
SCAN_ATTRIB_ANGLE = 'alpha'

# Default filenames for actions and params config files.
ACTIONS_FILENAME = 'actions.toml'
PARAMS_FILENAME = 'params.toml'


SPEC_EXT_SEARCH = '*.vpdata'


class GxsmTranslator(ConfigTranslator):
    """Handles device communication with the gxsm3 controller.

    Attributes:
        read_channels_config_path: path to config file to convert from
            raw data to physical units. See gxsmread documentation.
        read_use_physical_units: bool, whether or not to show the data
            in physical units. See gxsmread documentation.
        read_allow_convert_from_metadata: Use hardcoded info to convert
            some channels' data to physical units. See gxsmread.
        read_simplify_metadata: Whether or not to convert all metadata
            variables to attributes.  See gxsmread documentation.
        param_handler: ParamHandler instance used to handle parameters.
        action_handler: ActionHandler instance used to handle actions.

        last_scan_fname: Holds last filename to minimize loading files
            unnecessarily (basic cache check).
        old_scans: Holds last scans for cache purposes.
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
                 signal_mode: str = None,
                 **kwargs):
        """Initialize internal logic.

        Args:
            read_channels_config_path: path to config file to convert from
                raw data to physical units. See gxsmread documentation.
            read_use_physical_units: bool, whether or not to show the data
                in physical units. See gxsmread documentation.
            read_allow_convert_from_metadata: Use hardcoded info to convert
                some channels' data to physical units. See gxsmread.
            read_simplify_metadata: Whether or not to convert all metadata
                variables to attributes.  See gxsmread documentation.
            param_handler: ParamHandler to use. If None, spawns default.
            action_handler: ActionHandler to use. If None, spawns default.
            zctrl_channel: Feedback channel for ZCtrl. See gxsm/params.py.
            signal_mode: Signal mode to be running. See gxsm/actions.py.
        """
        self.read_channels_config_path = read_channels_config_path
        self.read_use_physical_units = read_use_physical_units
        self.read_allow_convert_from_metadata = read_allow_convert_from_metadata
        self.read_simplify_metadata = read_simplify_metadata

        self.last_scan_fname = ''
        self.old_scans = []
        self.last_spec_fname = ''
        self.old_spec = None

        # Default initialization of handlers and addition to kwargs
        if not action_handler:
            action_handler = _init_action_handler()
            if signal_mode:
                signal_enum = actions.GxsmSignalModeAction(signal_mode)
                actions.update_signal_mode(action_handler, signal_enum)
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
        last_scan_fname = None
        fnames = self._get_channel_filenames()

        # Avoid reloading scans if they are not new.
        there_are_scans = len(fnames) > 0
        scans_same_as_last = (there_are_scans and
                              fnames[0] == self.last_scan_fname)
        if there_are_scans and not scans_same_as_last:
            scans = []
            for fname in fnames:
                scan = self._load_scan(fname)
                if scan:
                    scans.append(scan)
            self.last_scan_fname = last_scan_fname
            self.old_scans = scans
        return self.old_scans

    def _get_channel_filenames(self) -> list[str]:
        """Request channel filenames from gxsm as list of strs."""
        channel_idx = 0
        fnames = []
        try:
            while channel_idx < self.MAX_NUM_CHANNELS:
                fname = gxsm.chfname(channel_idx)

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
        return fnames

    def _load_scan(self, fname: str) -> scan_pb2.Scan2d | None:
        """Try to load a scan from a given filename (None on error)."""
        ts = get_file_modification_datetime(fname)
        try:
            ds = read.open_dataset(
                fname, self.read_channels_config_path,
                self.read_use_physical_units,
                self.read_allow_convert_from_metadata,
                self.read_simplify_metadata,
                engine='scipy')

            # Grabbing first data variable, since each channel is
            # stored in its own file (so each file should have only
            # one data variable).
            scan = conv.convert_xarray_to_scan_pb2(
                ds[list(ds.data_vars)[0]])

            # Set ROI angle, timestamp, filename
            scan.params.spatial.roi.angle = ds.attrs[SCAN_ATTRIB_ANGLE]
            scan.timestamp.FromDatetime(ts)
            scan.filename = fname

            return scan
        except Exception as exc:
            logger.error(f"Could not read scan fname {fname}, "
                         f"got error {exc}.")
            return None

    def poll_spec(self) -> spec_pb2.Spec1d:
        """Override spec polling. For now, not supported."""
        spec_fname = self._get_latest_spec_filename()
        if spec_fname and spec_fname != self.last_spec_fname:
            spec = self._load_spec()
            if spec:
                self.last_spec_fname = spec_fname
                self.old_spec = spec
        return self.old_spec

    def _get_latest_spec_filename(self) -> str | None:
        """Obtain latest spec filename (or None if not found)."""
        chfname = gxsm.chfname(0)
        spec_search = (os.path.dirname(os.path.abspath(chfname)) + os.sep
                       + SPEC_EXT_SEARCH)
        files_list = glob.glob(spec_search)
        latest_spec = max(files_list, key=os.path.getctime)
        return latest_spec

    def _load_spec(self, fname: str) -> spec_pb2.Spec1d | None:
        """Load Spec1d from provided filename (None on failure)."""
        ts = get_file_modification_datetime(fname)
        try:
            df = read.open_spec(fname)
            spec = convert_dataframe_to_spec1d(df)

            spec.timestamp.FromDateTime(ts)
            spec.filename = fname
            return spec
        except Exception as exc:
            logger.error(f"Could not read spec fname {fname}, "
                         f"got error {exc}.")
            return None


def convert_dataframe_to_spec1d(df: pd.DataFrame) -> spec_pb2.Spec1d:
    """Convert pandas DataFrame to spec_pb2.Spec1d."""
    point_2d = geometry_pb2.Point2d(x=float(df.attrs[spec.PROBE_POS_X]),
                                    y=float(df.attrs[spec.PROBE_POS_Y]))
    probe_pos = spec_pb2.ProbePosition(point=point_2d,
                                       units=df.attrs[spec.PROBE_POS_UNIT])

    units_dict = df.attrs[spec.KEY_UNITS]
    names = list(units_dict.keys())
    units = list(units_dict.values())
    data = df.values

    spec_data = spec_pb2.SpecData(num_variables=data.shape[0],
                                  data_per_variable=data.shape[1],
                                  names=names, units=units,
                                  values=data.ravel().tolist())

    spec = spec_pb2.Spec1d(position=probe_pos,
                           data=spec_data)
    return spec


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
    scanning = s & (2+4) > GxsmTranslator.STATE_RUNNING_THRESH
    specing = s & 8 > GxsmTranslator.STATE_RUNNING_THRESH
    moving = s & 16 > GxsmTranslator.STATE_RUNNING_THRESH

    # TODO: investigate motor logic further...
    motor_running = (param_handler.get_param_spm(params.MOTOR_PARAM) <
                     GxsmTranslator.MOTOR_RUNNING_THRESH)
    if motor_running:
        return scan_pb2.ScopeState.SS_COARSE_MOTOR
    if scanning:
        return scan_pb2.ScopeState.SS_SCANNING
    if specing:
        return scan_pb2.ScopeState.SS_SPEC
    if moving:
        return scan_pb2.ScopeState.SS_MOVING
    return scan_pb2.ScopeState.SS_FREE


def _init_action_handler() -> ActionHandler:
    """Initialize GXSM action handler pointing to defulat config."""
    actions_config_path = os.path.join(os.path.dirname(__file__),
                                       ACTIONS_FILENAME)
    return ActionHandler(actions_config_path)


def _init_param_handler() -> params.GxsmParameterHandler:
    """Initialize GXSM action handler pointing to defulat config."""
    params_config_path = os.path.join(os.path.dirname(__file__),
                                      PARAMS_FILENAME)
    return params.GxsmParameterHandler(params_config_path)
