"""Handles device communication with the gxsm3 controller."""

import os.path
import logging
from typing import Any
import zmq
from pint import UndefinedUnitError
from google.protobuf.message import Message

from afspm.components.device.controller import (DeviceController,
                                                get_file_modification_datetime)

#from . import gxsmconstants as const
from afspm.utils import units
from afspm.utils import array_converters as conv
from afspm.io.protos.generated import scan_pb2
from afspm.io.protos.generated import control_pb2
from afspm.io.protos.generated import feedback_pb2

import gxsm  # Dynamic DLL, so not in pyproject.
from gxsmread import read


logger = logging.getLogger(__name__)


class GxsmController(DeviceController):
    """Handles device communication with the gxsm3 controller.

    Note that GXSM does not currently feed the following info via its remote
    API:
    - Chosen physical units: this could be angstrom, nm, etc. This is set in
    Preferences->User->User/XYUnit.
    - Whether the z-control feedback is currently on/off. This is set in
    DSP->Advanced->Z-Control->Enable feedback controller.

    Since these cannot be set via the API, we read these via the constructor.
    """

    GET_FAILURE = '\x04'
    TL_X = 'OffsetX'
    TL_Y = 'OffsetY'
    SZ_X = 'RangeX'
    SZ_Y = 'RangeY'
    RES_X = 'PointsX'
    RES_Y = 'PointsY'
    SCAN_SPEED_UNITS_S = 'dsp-fbs-scan-speed-scan'
    CP = 'dsp-fbs-cp'
    CI = 'dsp-fbs-ci'

    STATE_RUNNING_THRESH = 0
    MOTOR_RUNNING_THRESH = -2

    MAX_NUM_CHANNELS = 6
    CHFNAME_ERROR_STR = 'EE: invalid channel'

    # NOTE: I'm thinking each side is responsible for converting the data to the
    # units they want
    def __init__(self,
                 read_channels_config_path: str = None,
                 read_use_physical_units: bool = True,
                 read_allow_convert_from_metadata: bool = False,
                 read_simplify_metadata: bool = True,
                 gxsm_physical_units: str = 'angstrom',
                 is_zctrl_feedback_on: bool = True, **kwargs):
        self.read_channels_config_path = read_channels_config_path
        self.read_use_physical_units = read_use_physical_units
        self.read_allow_convert_from_metadata = read_allow_convert_from_metadata
        self.read_simplify_metadata = read_simplify_metadata
        self.gxsm_physical_units = gxsm_physical_units  # TODO: read from gxsm?
        self.is_zctrl_feedback_on = is_zctrl_feedback_on  # TODO: read from gxsm?

        self.last_scan_fname = ''
        self.old_scans = []

        super().__init__(**kwargs)

    def on_start_scan(self):
        gxsm.startscan()
        return control_pb2.ControlResponse.REP_SUCCESS

    def on_stop_scan(self):
        gxsm.stopscan()
        return control_pb2.ControlResponse.REP_SUCCESS

    def on_set_scan_params(self, scan_params: scan_pb2.ScanParameters2d
                           ) -> control_pb2.ControlResponse:
        attrs = [self.TL_X, self.TL_Y, self.SZ_X, self.SZ_Y, self.RES_X,
                 self.RES_Y]
        vals = [scan_params.spatial.roi.top_left.x,
                scan_params.spatial.roi.top_left.y,
                scan_params.spatial.roi.size.x,
                scan_params.spatial.roi.size.y,
                scan_params.data.shape.x,
                scan_params.data.shape.y]
        attr_units = [scan_params.spatial.units,
                      scan_params.spatial.units,
                      scan_params.spatial.units,
                      scan_params.spatial.units,
                      None, None]
        gxsm_units = [self.gxsm_physical_units,
                      self.gxsm_physical_units,
                      self.gxsm_physical_units,
                      self.gxsm_physical_units,
                      None, None]

        # Note: when setting scan params, data units don't matter! These
        # are only important in explicit scans. When setting scan params,
        # we only care about the data shape, which is pixel-units.
        if self._gxsm_set_list(attrs, vals, attr_units, gxsm_units):
            return control_pb2.ControlResponse.REP_SUCCESS
        return control_pb2.ControlResponse.REP_ATTRIB_ERROR

    def on_set_zctrl_params(self, zctrl_params: feedback_pb2.ZCtrlParameters
                            ) -> control_pb2.ControlResponse:
        """Note: there is no error handling, so always return success."""
        self._gxsm_set(self.CP, zctrl_params.proportionalGain)
        self._gxsm_set(self.CI, zctrl_params.integralGain)
        return control_pb2.ControlResponse.REP_SUCCESS

    def poll_scan_state(self) -> scan_pb2.ScanState:
        """Returns current scan state in accordance with system model."""
        # Note: updating self.scan_state is handled by the calling method
        # in DeviceController.
        state = self._get_current_scan_state()
        if (self.scan_state == scan_pb2.ScanState.SS_SCANNING and
                state  == scan_pb2.ScanState.SS_FREE):
            gxsm.autosave()  # Save the images we have recorded
        return state

    def poll_scan_params(self) -> scan_pb2.ScanParameters2d:
        return self._get_current_scan_params(self.gxsm_physical_units)

    def poll_scans(self) -> [scan_pb2.Scan2d]:
        channel_idx = 0
        fnames = []
        try:
            while channel_idx < self.MAX_NUM_CHANNELS:
                fname = gxsm.chfname(channel_idx)

                # Handle comparing against last scan
                if channel_idx == 0:
                    if fname == self.last_scan_fname:
                        break
                    self.last_scan_fname = fname

                # Break if on last 'set' channel
                if fname == self.CHFNAME_ERROR_STR:
                    break

                # Only append actually saved files
                if os.path.isfile(fname):
                    fnames.append(fname)
                channel_idx += 1
        except Exception as exc:
            logger.trace("Exception with requesting channel %s: %s",
                         channel_idx, str(exc))

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
                    logger.error("Could not read scan fname %s, got error %s.",
                                 fname, exc)
                    continue
                scan = conv.convert_xarray_to_scan_pb2(
                    ds[list(ds.data_vars)[0]])  # Grabbing first data variable
                scan.timestamp.FromDatetime(ts)
                scans.append(scan)
            self.old_scans = scans
            return scans
        return self.old_scans

    def poll_zctrl_params(self) -> feedback_pb2.ZCtrlParameters:
        """Poll the controller for the current Z-Control parameters."""
        return self._get_current_zctrl_params(self.is_zctrl_feedback_on)

    @staticmethod
    def _get_current_zctrl_params(is_zctrl_feedback_on: bool):
        """Poll gxsm for current ZCtrl Params and fill object."""
        zctrl_params = feedback_pb2.ZCtrlParameters()
        zctrl_params.feedbackOn = is_zctrl_feedback_on
        zctrl_params.proportionalGain = gxsm.get(GxsmController.CP)
        zctrl_params.integralGain = gxsm.get(GxsmController.CI)
        return zctrl_params

    # TODO: Should this just be poll_scan_params???
    @staticmethod
    def _get_current_scan_params(gxsm_phys_units: str
                                 ) -> scan_pb2.ScanParameters2d:
        """Poll gxsm for current scan parameters and fill object."""
        scan_params = scan_pb2.ScanParameters2d()
        scan_params.spatial.roi.top_left.x = gxsm.get(GxsmController.TL_X)
        scan_params.spatial.roi.top_left.y = gxsm.get(GxsmController.TL_Y)
        scan_params.spatial.roi.size.x = gxsm.get(GxsmController.SZ_X)
        scan_params.spatial.roi.size.y = gxsm.get(GxsmController.SZ_Y)
        scan_params.spatial.units = gxsm_phys_units

        # Note: all gxsm attributes returned as float, must convert to int
        scan_params.data.shape.x = int(gxsm.get(GxsmController.RES_X))
        scan_params.data.shape.y = int(gxsm.get(GxsmController.RES_Y))
        # Not setting data uits, as these are linked to scan channel

        return scan_params

    @staticmethod
    def _gxsm_set(attr: str, val: Any, curr_units: str = None,
                  gxsm_units: str = None):
        """Convert a value to gxsm units and set it."""
        if curr_units and gxsm_units and curr_units != gxsm_units:
            val = units.convert(val, curr_units,
                                gxsm_units)
        gxsm.set(attr, str(val))

    @staticmethod
    def _gxsm_set_list(attrs: list[str], vals: list[Any],
                       curr_units: list[str | None],
                       gxsm_units: list[str | None]) -> bool:
        """Convert a list of values to gxsm units and set them."""
        converted_vals = []
        for val, curr_unit, gxsm_unit in zip(vals, curr_units, gxsm_units):
            if curr_unit and gxsm_unit and curr_unit != gxsm_unit:
                try:
                    converted_vals.append(
                        units.convert(val, curr_unit, gxsm_unit))
                except UndefinedUnitError:
                    logger.error("Unable to convert %s from %s to %s.",
                                 val, curr_unit, gxsm_unit)
                    return False
            else:
                converted_vals.append(val)
        for val, attr in zip(converted_vals, attrs):
            gxsm.set(attr, str(val))
        return True


    @staticmethod
    def _get_current_scan_state() -> scan_pb2.ScanState:
        """Returns the current scan state.

        This queries gxsm for its current scan state.

        Returns:
            ScanState.
        """
        svec = gxsm.rtquery('s')
        s = int(svec[0])
        # (2+4) == Scanning; 8 == Vector Probe
        scanning = (s & (2+4) > GxsmController.STATE_RUNNING_THRESH or
                    s & 8 > GxsmController.STATE_RUNNING_THRESH)
        moving = s & 16 > GxsmController.STATE_RUNNING_THRESH

        # TODO: investigate motor logic further...
        motor_running = (gxsm.get("dsp-fbs-motor") <
                         GxsmController.MOTOR_RUNNING_THRESH)

        if motor_running:
            return scan_pb2.ScanState.SS_MOTOR_RUNNING
        if scanning:
            return scan_pb2.ScanState.SS_SCANNING
        if moving:
            return scan_pb2.ScanState.SS_MOVING
        return scan_pb2.ScanState.SS_FREE
