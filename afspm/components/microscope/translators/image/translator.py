"""MicroscopeTranslator that shows scans from an image."""

import time
import logging
from pathlib import Path
from os import sep

import xarray as xr
import numpy as np
from google.protobuf.message import Message

from ...translator import MicroscopeTranslator
from .....io.protos.generated import scan_pb2
from .....io.protos.generated import control_pb2
from .....io.protos.generated import feedback_pb2
from .....utils import array_converters as ac


logger = logging.getLogger(__name__)


class ImageTranslator(MicroscopeTranslator):
    """Simulates a MicroscopeTranslator with an individual image.

    This translator loads a single image as if it was a 2D scan, allowing
    scans to be performed within the image 'scan range' as provided.

    Attributes:
        dev_img: loaded image, as an xarray DataArray.
        dev_scope_state: current scanning state.
        dev_scan_params: current scan parameters.
        dev_scan: latest scan.

        scan_time_s: how long a scan should take, in seconds.
        move_time_s: how long changing scan paramters should take, in seconds.
        start_ts: a timestamp for timing the scan and move durations.
    """

    _DEFAULT_IMG_PATH = (str(Path(__file__).parent.resolve()) + sep + "data" +
                         sep + "peppers.tiff")

    def __init__(self, physical_origin: tuple[float, float],
                 physical_size: tuple[float, float],
                 physical_units: str, data_units: str,
                 scan_time_s: float, move_time_s: float,
                 img_path: str = _DEFAULT_IMG_PATH, **kwargs):
        """Initialize translator.

        Args:
            img_path: path to image to load.
            physical_origin: physical origin as top-left (x,y).
            physical_size: physical size as (width, height).
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

        self.dev_img = ac.create_xarray_from_img_path(img_path,
                                                      physical_origin,
                                                      physical_size,
                                                      physical_units,
                                                      data_units)
        self.dev_scope_state = scan_pb2.ScopeState.SS_FREE
        self.dev_scan_params = scan_pb2.ScanParameters2d()
        self.dev_scan = None
        self.file_id = 0
        super().__init__(**kwargs)

    def on_start_scan(self):
        self.start_ts = time.time()
        self.dev_scope_state = scan_pb2.ScopeState.SS_COLLECTING
        return control_pb2.ControlResponse.REP_SUCCESS

    def on_stop_scan(self):
        self.start_ts = None
        self.dev_scope_state = scan_pb2.ScopeState.SS_FREE
        return control_pb2.ControlResponse.REP_SUCCESS

    def on_set_scan_params(self, scan_params: scan_pb2.ScanParameters2d
                           ) -> control_pb2.ControlResponse:
        self.start_ts = time.time()
        self.dev_scope_state = scan_pb2.ScopeState.SS_MOVING
        self.dev_scan_params = scan_params
        return control_pb2.ControlResponse.REP_SUCCESS

    def on_set_zctrl_params(self, zctrl_params: feedback_pb2.ZCtrlParameters
                            ) -> control_pb2.ControlResponse:
        """Z-Ctrl doesn't do anything with images, not supported."""
        return control_pb2.ControlResponse.REP_CMD_NOT_SUPPORTED

    def on_param_request(self, param: control_pb2.ParameterMsg
                         ) -> (control_pb2.ControlResponse,
                               Message | int | None):
        """Not supported, returning message indicating as much."""
        return control_pb2.REP_CMD_NOT_SUPPORTED, None

    def poll_scope_state(self) -> scan_pb2.ScopeState:
        return self.dev_scope_state

    def poll_scan_params(self) -> scan_pb2.ScanParameters2d:
        return self.dev_scan_params

    def poll_zctrl_params(self) -> feedback_pb2.ZCtrlParameters:
        """Z-Ctrl doesn't do anything with images, not supported."""
        return feedback_pb2.ZCtrlParameters()

    def poll_scans(self) -> [scan_pb2.Scan2d]:
        return [self.dev_scan] if self.dev_scan else []

    def run_per_loop(self):
        """Main loop, where we indicate when scans and moves are done."""
        if self.start_ts:
            duration = None
            update_scan = False
            if self.dev_scope_state == scan_pb2.ScopeState.SS_COLLECTING:
                duration = self.scan_time_s
                update_scan = True
            elif self.dev_scope_state == scan_pb2.ScopeState.SS_MOVING:
                duration = self.move_time_s

            if duration:
                curr_ts = time.time()
                if curr_ts - self.start_ts > duration:
                    self.start_ts = None
                    self.dev_scope_state = scan_pb2.ScopeState.SS_FREE
                    if update_scan:
                        self.update_scan()
                        self.dev_scan.timestamp.GetCurrentTime()
                        self.dev_scan.filename = self._simulate_filename()
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
        units = self.dev_scan_params.spatial.length_units
        da = xr.DataArray(data=None, dims=['y', 'x'],
                          coords={'y': y, 'x': x})
        da.x.attrs['units'] = units
        da.y.attrs['units'] = units

        img = self.dev_img.interp(x=da.x, y=da.y)
        self.dev_scan = ac.convert_xarray_to_scan_pb2(img)

    def _simulate_filename(self) -> str:
        """Simulate a fake filename, for testing.

        We expecct filenames to be stored in Scan2d, so we can better log
        metadata. Thus, we simulate a filename here.
        """
        fname = './' + str(self.file_id) + '.png'
        self.file_id += 1
        return fname
