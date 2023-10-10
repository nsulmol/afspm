"""Handles device communication with the gxsm3 controller."""

import logging
from typing import Any
import zmq
from google.protobuf.message import Message

from afspm.components.device.controller import (DeviceController,
                                                get_file_creation_datetime)

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
        try:
            self._gxsm_set(self.TL_X, scan_params.spatial.roi.top_left.x,
                           scan_params.spatial.units)
            self._gxsm_set(self.TL_Y, scan_params.spatial.roi.top_left.y,
                           scan_params.spatial.units)

            self._gxsm_set(self.SZ_X, scan_params.spatial.roi.size.x,
                           scan_params.spatial.units)
            self._gxsm_set(self.SZ_Y, scan_params.spatial.roi.size.y,
                           scan_params.spatial.units)

            # if scan_params.spatial.HasField(scan_speed_u)
            #self._gxsm_set(self.SCAN_SPEED_UNITS_S,
            #               scan_params.spatial.scan_speed_units_s,
            #               scan_params.spatial.units)

            self._gxsm_set(self.RES_X, scan_params.data.shape.x)
            self._gxsm_set(self.RES_Y, scan_params.data.shape.y)
        except Exception as exc:
            logger.error("Failure on setting scan params: %s", exc)
            return control_pb2.ControlResponse.REP_FAILURE

        return control_pb2.ControlResponse.REP_SUCCESS

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
                ts = get_file_creation_datetime(fname)
                ds = read.opendataset(fname, self.channels_config_path)
                scan = conv.convert_xarray_to_scan_pb2(
                    ds[list(ds.data_vars)[0]])
                scan.timestamp.FromDatetime(ts)
                scans.append(scan)
            self.old_scans = scans
            return scans
        return self.old_scans


    # TODO: Should this just be poll_scan_params???
    def _get_current_scan_params(self):
        scan_params = scan_pb2.ScanParameters2d()
        # TODO: how to handle units??  For now, just send out how they are received?
        scan_params.spatial.roi.top_left.x = gxsm.get(self.TL_X)
        scan_params.spatial.roi.top_left.y = gxsm.get(self.TL_Y)
        scan_params.spatial.roi.size.x = gxsm.get(self.SZ_X)
        scan_params.spatial.roi.size.y = gxsm.get(self.SZ_Y)
        scan_params.spatial.units = self.GXSM_PHYS_UNITS

        #scan_params.spatial.scan_speed_units_s = gxsm.get(
        #    self.SCAN_SPEED_UNITS_S)

        scan_params.data.shape.x = gxsm.get(self.RES_X)
        scan_params.data.shape.y = gxsm.get(self.RES_Y)
        #scan_params.data.units = ???  # TODO: how to read units?

        return scan_params

    def _gxsm_set(self, attr: str, val: Any, curr_units: str = None):
        # If curr_units is None, we don't convert
        if curr_units:
            val = units.convert(val, curr_units, self.GXSM_PHYS_UNITS)
        gxsm.set(attr, str(val))

    def _get_current_scan_state(self) -> scan_pb2.ScanState:
        """Returns the current scan state.

        This queries gxsm for its current scan state.

        Returns:
            ScanState.
        """
        svec = gxsm.rtquery('s')
        s = int(svec[0])
        scanning = s & (2+4) > 0 or s & 8 > 0  # 8 == Vector Probe
        moving = s & 16 > self.STATE_RUNNING_THRESH
        motor_running = gxsm.get("dsp-fbs-motor") > self.MOTOR_RUNNING_THRESH

        if motor_running:
            return scan_pb2.ScanState.SS_MOTOR_RUNNING
        if scanning:
            return scan_pb2.ScanState.SS_SCANNING
        if moving:
            return scan_pb2.ScanState.SS_MOVING
        return scan_pb2.ScanState.SS_FREE


# Spawn settings
CONFIG_FILE = './config.toml'
COMPONENT_TO_SPAWN = 'devcon'
LOG_FILE = 'log.txt'
LOG_TO_STDOUT = 'True'
LOG_LEVEL = 'INFO'

if __name__ == '__main__':
    from afspm import spawn
    spawn.spawn_monitorless_component(CONFIG_FILE,
                                      COMPONENT_TO_SPAWN,
                                      LOG_FILE,
                                      LOG_TO_STDOUT,
                                      LOG_LEVEL)
