"""Handles device communication with the gxsm3 controller."""

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

import gxsm  # Dynamic DLL, so not in pyproject.
from gxsmread import read


logger = logging.getLogger(__name__)


class GxsmController(DeviceController):
    """handles device communication with the gxsm3 controller.
    """

    GET_FAILURE = '\x04'
    GXSM_PHYS_UNITS = 'angstrom'  # Unit the gxsm data is in #TODO Confirm!!!
    TL_X = 'OffsetX'
    TL_Y = 'OffsetY'
    SZ_X = 'RangeX'
    SZ_Y = 'RangeY'
    RES_X = 'PointsX'
    RES_Y = 'PointsY'
    SCAN_SPEED_UNITS_S = 'dsp-fbs-scan-speed-scan'

    STATE_RUNNING_THRESH = 0
    MOTOR_RUNNING_THRESH = -2

    MAX_NUM_CHANNELS = 6

    # NOTE: I'm thinking each side is responsible for converting the data to the
    # units they want
    def __init__(self,
                 channels_config_path: str = "./channels_config.toml",
                 **kwargs):
        #, default_physical_units: str = 'nanometer'):
        self.channels_config_path = channels_config_path
        #self.sent_physical_units = default_physical_units
        self.last_scan_fname = ''
        self.last_scan_state = scan_pb2.ScanState.SS_UNDEFINED

        super().__init__(**kwargs)

    def on_start_scan(self):
        gxsm.startscan()
        return  control_pb2.ControlResponse.REP_SUCCESS

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

        if self._gxsm_set_list(attrs, vals, attr_units):
            return control_pb2.ControlResponse.REP_SUCCESS
        return control_pb2.ControlResponse.REP_ATTRIB_ERROR

    def poll_scan_state(self) -> scan_pb2.ScanState:
        """Returns current scan state in accordance with system model."""
        state = self._get_current_scan_state()
        if (self.last_scan_state == scan_pb2.ScanState.SS_SCANNING and
                state  == scan_pb2.ScanState.SS_FREE):
            gxsm.autosave()  # Save the images we have recorded
            self.last_scan_state = state
        return state

    def poll_scan_params(self) -> scan_pb2.ScanParameters2d:
        return self._get_current_scan_params()

    def poll_scans(self) -> [scan_pb2.Scan2d]:
        channel_idx = 0
        fnames = []
        try:
            # Get the filename for the first channel scan
            fname = gxsm.chfname(channel_idx)

            if fname != self.last_scan_fname:
                self.last_scan_fname = fname
                while channel_idx < self.MAX_NUM_CHANNELS:
                    fnames.append(fname)
                    channel_idx += 1
                    fname = gxsm.chfname(channel_idx)
        except Exception as exc:
            logger.trace("Exception with requesting channel %s: %s",
                         channel_idx, str(exc))

        # Avoid reloading scans if they are not new.
        if len(fnames) > 0:
            scans = []
            for fname in fnames:
                ts = get_file_modification_datetime(fname)
                ds = read.opendataset(fname, self.channels_config_path)
                scan = conv.convert_xarray_to_scan_pb2(
                    ds[list(ds.data_vars)[0]])
                scan.timestamp.FromDatetime(ts)
                scans.append(scan)
            self.old_scans = scans
            return scans
        return self.old_scans


    # TODO: Should this just be poll_scan_params???
    @staticmethod
    def _get_current_scan_params():
        scan_params = scan_pb2.ScanParameters2d()
        # TODO: how to handle units??  For now, just send out how they are received?
        scan_params.spatial.roi.top_left.x = gxsm.get(GxsmController.TL_X)
        scan_params.spatial.roi.top_left.y = gxsm.get(GxsmController.TL_Y)
        scan_params.spatial.roi.size.x = gxsm.get(GxsmController.SZ_X)
        scan_params.spatial.roi.size.y = gxsm.get(GxsmController.SZ_Y)
        scan_params.spatial.units = GxsmController.GXSM_PHYS_UNITS

        # Note: all gxsm attributes returned as float, must convert to int
        scan_params.data.shape.x = int(gxsm.get(GxsmController.RES_X))
        scan_params.data.shape.y = int(gxsm.get(GxsmController.RES_Y))
        #scan_params.data.units = ???  # TODO: how to read units?

        return scan_params

    @staticmethod
    def _gxsm_set(attr: str, val: Any, curr_units: str = None):
        """Convert a value to gxsm units and set it."""
        # If curr_units is None, we don't convert
        if curr_units:
            val = units.convert(val, curr_units,
                                GxsmController.GXSM_PHYS_UNITS)
        gxsm.set(attr, str(val))

    @staticmethod
    def _gxsm_set_list(attrs: list[str], vals: list[Any],
                       curr_units: list[str | None]) -> bool:
        """Convert a list of values to gxsm units and set them."""
        converted_vals = []
        for val, curr_units in zip([vals, curr_units]):
            if curr_units:
                try:
                    converted_vals.append(
                        units.convert(val, curr_units,
                                      GxsmController.GXSM_PHYS_UNITS))
                except UndefinedUnitError:
                    logger.error("Unable to convert %s from %s to %s.",
                                 val, curr_units,
                                 GxsmController.GXSM_PHYS_UNITS)
                    return False
            else:
                converted_vals.append(val)
        for val, attr in zip([converted_vals, attrs]):
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
