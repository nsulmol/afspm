"""Handles device communication with the gxsm3 controller."""

import os.path
import logging

from afspm.components.microscope.translator import (
    MicroscopeTranslator, get_file_modification_datetime)
from afspm.components.microscope.translators.gxsm.params import (
    PARAM_METHOD_MAP, get_param_list, set_param_list, GxsmParameter,
    get_param)

from afspm.utils import array_converters as conv
from afspm.io.protos.generated import scan_pb2
from afspm.io.protos.generated import control_pb2
from afspm.io.protos.generated import feedback_pb2

import gxsm  # Dynamic DLL, so not in pyproject.
from gxsmread import read


logger = logging.getLogger(__name__)


# Attributes from the read scan file (differs from params.GxsmParameter, which
# contains UUIDs for getting/setting parameters).
SCAN_ATTRIB_ANGLE = 'alpha'


class GxsmTranslator(MicroscopeTranslator):
    """Handles device communication with the gxsm3 controller.

    Note that GXSM does not currently feed the following info via its remote
    API:
    - Chosen physical units: this could be angstrom, nm, etc. This is set in
    Preferences->User->User/XYUnit.
    - Whether the z-control feedback is currently on/off. This is set in
    DSP->Advanced->Z-Control->Enable feedback controller.

    Since these cannot be set via the API, we read these via the constructor.
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
                 gxsm_physical_units: str = 'angstrom',
                 is_zctrl_feedback_on: bool = True, **kwargs):
        """Initialize internal logic."""
        self.read_channels_config_path = read_channels_config_path
        self.read_use_physical_units = read_use_physical_units
        self.read_allow_convert_from_metadata = read_allow_convert_from_metadata
        self.read_simplify_metadata = read_simplify_metadata
        self.gxsm_physical_units = gxsm_physical_units  # TODO: read from gxsm?
        self.is_zctrl_feedback_on = is_zctrl_feedback_on  # TODO: read from gxsm?

        self.last_scan_fname = ''
        self.old_scans = []

        super().__init__(**kwargs)
        self.param_method_map = PARAM_METHOD_MAP

    def on_start_scan(self):
        """Override on starting scan."""
        gxsm.startscan()
        return control_pb2.ControlResponse.REP_SUCCESS

    def on_stop_scan(self):
        """Override on stopping scan."""
        gxsm.stopscan()
        return control_pb2.ControlResponse.REP_SUCCESS

    def on_set_scan_params(self, scan_params: scan_pb2.ScanParameters2d
                           ) -> control_pb2.ControlResponse:
        """Override on setting scan params."""
        # We *must* set x-values before y-values, because gxsm will scale the
        # linked y when its x is set. Somewhat confusing, in my opinion.
        attrs = [GxsmParameter.TL_X, GxsmParameter.TL_Y,
                 GxsmParameter.SZ_X, GxsmParameter.SZ_Y,
                 GxsmParameter.RES_X, GxsmParameter.RES_Y,
                 GxsmParameter.ANGLE,]
        vals = [scan_params.spatial.roi.top_left.x,
                scan_params.spatial.roi.top_left.y,
                scan_params.spatial.roi.size.x,
                scan_params.spatial.roi.size.y,
                scan_params.data.shape.x,
                scan_params.data.shape.y,
                scan_params.spatial.roi.angle]
        attr_units = [scan_params.spatial.units,
                      scan_params.spatial.units,
                      scan_params.spatial.units,
                      scan_params.spatial.units,
                      None, None, scan_params.spatial.units]
        gxsm_units = [self.gxsm_physical_units,
                      self.gxsm_physical_units,
                      self.gxsm_physical_units,
                      self.gxsm_physical_units,
                      None, None, self.gxsm_physical_units]

        # Note: when setting scan params, *data* units don't matter! These
        # are only important in explicit scans. When setting scan params,
        # we only care about the data shape, which is pixel-units.
        if set_param_list(attrs, vals, attr_units, gxsm_units):
            return control_pb2.ControlResponse.REP_SUCCESS
        return control_pb2.ControlResponse.REP_PARAM_ERROR

    def on_set_zctrl_params(self, zctrl_params: feedback_pb2.ZCtrlParameters
                            ) -> control_pb2.ControlResponse:
        """Note: there is no error handling, so always return success."""
        # We *must* set CI before CP, because gxsm will scale CP when
        # CI is set. Somewhat confusing, in my opinion.
        nones = [None, None]
        if set_param_list([GxsmParameter.CI, GxsmParameter.CP],
                          [zctrl_params.integralGain,
                           zctrl_params.proportionalGain], nones, nones):
            return control_pb2.ControlResponse.REP_SUCCESS
        return control_pb2.ControlResponse.REP_PARAM_ERROR

    def poll_scope_state(self) -> scan_pb2.ScopeState:
        """Return current scope state in accordance with system model."""
        # Note: updating self.scope_state is handled by the calling method
        # in MicroscopeTranslator.
        state = self._get_current_scope_state()
        if (self.scope_state == scan_pb2.ScopeState.SS_COLLECTING and
                state == scan_pb2.ScopeState.SS_FREE):
            gxsm.autosave()  # Save the images we have recorded
        return state

    def poll_scan_params(self) -> scan_pb2.ScanParameters2d:
        """Override scan params polling."""
        vals = get_param_list([GxsmParameter.TL_X, GxsmParameter.TL_Y,
                               GxsmParameter.SZ_X, GxsmParameter.SZ_Y,
                               GxsmParameter.RES_X, GxsmParameter.RES_Y,
                               GxsmParameter.ANGLE])

        scan_params = scan_pb2.ScanParameters2d()
        scan_params.spatial.roi.top_left.x = vals[0]
        scan_params.spatial.roi.top_left.y = vals[1]
        scan_params.spatial.roi.size.x = vals[2]
        scan_params.spatial.roi.size.y = vals[3]
        scan_params.spatial.roi.angle = vals[6]
        scan_params.spatial.units = self.gxsm_physical_units

        # Note: all gxsm attributes returned as float, must convert to int
        scan_params.data.shape.x = int(vals[4])
        scan_params.data.shape.y = int(vals[5])
        # Not setting data units, as these are linked to scan channel
        return scan_params

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

    def poll_zctrl_params(self) -> feedback_pb2.ZCtrlParameters:
        """Poll the controller for the current Z-Control parameters."""
        vals = get_param_list([GxsmParameter.CP, GxsmParameter.CI])

        zctrl_params = feedback_pb2.ZCtrlParameters()
        zctrl_params.feedbackOn = self.is_zctrl_feedback_on
        zctrl_params.proportionalGain = vals[0]
        zctrl_params.integralGain = vals[1]
        return zctrl_params

    @staticmethod
    def _get_current_scope_state() -> scan_pb2.ScopeState:
        """Return the current scope state.

        This queries gxsm for its current scope state.

        Returns:
            ScopeState, or None if query fails.
        """
        svec = gxsm.rtquery('s')
        s = int(svec[0])
        # (2+4) == Scanning; 8 == Vector Probe
        scanning = (s & (2+4) > GxsmTranslator.STATE_RUNNING_THRESH or
                    s & 8 > GxsmTranslator.STATE_RUNNING_THRESH)
        moving = s & 16 > GxsmTranslator.STATE_RUNNING_THRESH

        # TODO: investigate motor logic further...
        motor_running = (get_param(GxsmParameter.MOTOR) <
                         GxsmTranslator.MOTOR_RUNNING_THRESH)
        if motor_running:
            return scan_pb2.ScopeState.SS_MOTOR_RUNNING
        if scanning:
            return scan_pb2.ScopeState.SS_COLLECTING
        if moving:
            return scan_pb2.ScopeState.SS_MOVING
        return scan_pb2.ScopeState.SS_FREE
