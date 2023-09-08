"""DeviceController that shows scans from an image."""

import copy
import time
import logging

from google.protobuf.message import Message

from afspm.spawn import LOGGER_ROOT
from afspm.components.device_controller import DeviceController
from afspm.io.protos.generated import scan_pb2
from afspm.io.protos.generated import control_pb2
from afspm.utils import array_converters as ac

import xarray as xr
import numpy as np
import imageio.v3 as iio


logger = logging.getLogger(LOGGER_ROOT + ".examples.image_roi." + __name__)


class ImageDeviceController(DeviceController):
    """Simulates a DeviceController with an individual image.

    This controller loads a single image as if it was a 2D scan, allowing
    scans to be performed within the image 'scan range' as provided.

    Attributes:
        dev_img: loaded image, as an xarray DataArray.
        dev_scan_state: current scanning state.
        dev_scan_params: current scan parameters.
        dev_scan: latest scan.

        scan_time_s: how long a scan should take, in seconds.
        move_time_s: how long changing scan paramters should take, in seconds.
        start_ts: a timestamp for timing the scan and move durations.
    """
    def __init__(self, img_path: str,
                 physical_dims: tuple[float, float, float, float],
                 physical_units: str, data_units: str,
                 scan_time_s: float, move_time_s: float, **kwargs):
        """Initialize controller.

        Args:
            img_path: path to image to load.
            physical_dims: physical dimensions as [tl.x, tl.y, width, height].
            physical_units: the units of the physical dimensions (i.e. x/y
                dimension), str.
            data_units: the units of the scan data (i.e. z-dimension), str.
            scan_time_s: how long a scan should take, in seconds.
            move_time_s: how long changing scan paramters should take, in
                seconds.
        """
        self.start_ts = None
        self.scan_time_s = scan_time_s
        self.move_time_s = move_time_s

        self.dev_img = self.create_xarray_from_img_path(img_path,
                                                        physical_dims[0:2],
                                                        physical_dims[2:4],
                                                        physical_units,
                                                        data_units)
        self.dev_scan_state = scan_pb2.ScanState.SS_FREE
        self.dev_scan_params = scan_pb2.ScanParameters2d()
        self.dev_scan = scan_pb2.Scan2d()
        super().__init__(**kwargs)

    # TODO: Move to array converters?
    def create_xarray_from_img_path(self, img_path: str,
                                    tl: tuple[float, float],
                                    size: tuple[float, float],
                                    physical_units: str, data_units: str):
        """Create an xarray from the provided image and physical units data."""
        img = np.asarray(iio.imread(img_path))[:, :, 0]  # Only grab one channel

        x = np.linspace(tl[0], tl[0] + size[0], img.shape[0])
        y = np.linspace(tl[1], tl[1] + size[1], img.shape[1])

        da = xr.DataArray(data=img, dims=['y', 'x'],
                          coords={'y': y, 'x': x},
                          attrs={'units': data_units})
        da.x.attrs['units'] = physical_units
        da.y.attrs['units'] = physical_units

        return da

    def on_start_scan(self):
        self.start_ts = time.time()
        self.dev_scan_state = scan_pb2.ScanState.SS_SCANNING
        return control_pb2.ControlResponse.REP_SUCCESS

    def on_stop_scan(self):
        self.start_ts = None
        self.dev_scan_state = scan_pb2.ScanState.SS_FREE
        return control_pb2.ControlResponse.REP_SUCCESS

    def on_set_scan_params(self, scan_params: scan_pb2.ScanParameters2d
                           ) -> control_pb2.ControlResponse:
        self.start_ts = time.time()
        self.dev_scan_state = scan_pb2.ScanState.SS_MOVING
        self.dev_scan_params = scan_params
        return control_pb2.ControlResponse.REP_SUCCESS

    def poll_scan_state(self) -> scan_pb2.ScanState:
        return self.dev_scan_state

    def poll_scan_params(self) -> scan_pb2.ScanParameters2d:
        return self.dev_scan_params

    def poll_scan(self) -> scan_pb2.Scan2d:
        return self.dev_scan

    def run_per_loop(self):
        """Main loop, where we indicate when scans and moves are done."""
        if self.start_ts:
            duration = None
            update_scan = False
            if self.dev_scan_state == scan_pb2.ScanState.SS_SCANNING:
                duration = self.scan_time_s
                update_scan = True
            elif self.dev_scan_state == scan_pb2.ScanState.SS_MOVING:
                duration = self.move_time_s

            if duration:
                curr_ts = time.time()
                if curr_ts - self.start_ts > duration:
                    self.start_ts = None
                    self.dev_scan_state = scan_pb2.ScanState.SS_FREE
                    if update_scan:
                        self.update_scan()
                        self.dev_scan.timestamp.GetCurrentTime()
        super().run_per_loop()

    def update_scan(self):
        """Updates the latest scan based on the latest scan params."""
        tl = [self.dev_scan_params.spatial.roi.top_left.x,
              self.dev_scan_params.spatial.roi.top_left.y]
        size = [self.dev_scan_params.spatial.roi.size.x,
                self.dev_scan_params.spatial.roi.size.y]
        data_shape = [self.dev_scan_params.data.shape.x,
                      self.dev_scan_params.data.shape.y]

        x = np.linspace(tl[0], tl[0] + size[0], data_shape[0])
        y = np.linspace(tl[1], tl[1] + size[1], data_shape[1])

        # Wrapping in DataArray, to feed coordinates with units.
        # Alternatively, could just feed interp(x=x, y=y)
        units = self.dev_scan_params.spatial.units
        da = xr.DataArray(data=None, dims=['y', 'x'],
                          coords={'y': y, 'x': x})
        da.x.attrs['units'] = units
        da.y.attrs['units'] = units

        img = self.dev_img.interp(x=da.x, y=da.y)
        self.dev_scan = ac.convert_xarray_to_scan_pb2(img)
