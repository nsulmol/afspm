"""Handles device communication with aslyum research controllers."""

import os
import logging
import glob

from afspm.components.device.controller import (DeviceController,
                                                get_file_modification_datetime,
                                                DeviceError)

from afspm.components.device.controllers.asylum.client import XopClient
from afspm.components.device.controllers.asylum import params
from afspm.components.device.controllers.asylum.xop import (
    convert_igor_path_to_python_path)

from afspm.utils import array_converters as conv
from afspm.io.protos.generated import scan_pb2
from afspm.io.protos.generated import control_pb2
from afspm.io.protos.generated import feedback_pb2

import SciFiReaders as sr


logger = logging.getLogger(__name__)


class AsylumController(DeviceController):
    """Handles device communication with the asylum controller.

    The AsylumController communicates with the Asylum Research software via the
    XopClient, which sends/receives JSON messages over a zmq interface as
    defined by the Allen Institute's ZeroMQ-XOP project:
    https://github.com/AllenInstitute/ZeroMQ-XOP

    Attributes:
        xop_client: XOPClient for communicating with Asylum Research exe.
    """

    SCAN_PARAMS = (params.AsylumParam.TL_X, params.AsylumParam.TL_Y,
                   params.AsylumParam.SCAN_SIZE,
                   params.AsylumParam.SCAN_X_RATIO,
                   params.AsylumParam.SCAN_Y_RATIO,
                   params.AsylumParam.RES_X, params.AsylumParam.RES_Y)

    ZCTRL_PARAMS = (params.AsylumParam.CP,
                    params.AsylumParam.CI)

    IMG_EXT = ".ibw"

    def __init__(self, xop_client: XopClient, **kwargs):
        """Init things, ensuer we can hook into XOP Client."""
        if xop_client is None:
            msg = "No xop client provided, cannot continue!"
            logger.critical(msg)
            raise AttributeError(msg)

        self._client = xop_client
        self._old_scan_path = None
        self._old_scans = []

        self._old_save_state = None
        self._setup_saving()

        super().__init__(**kwargs)

    def __del__(self):
        """Handle object destruction: reset what we changed on startup."""
        # Reset save state!
        if not params.set_param(self._client,
                                params.AsylumParam.SAVE_IMAGE,
                                self._old_save_state):
            msg = "Was unable to reset SaveImage state on closure!"
            logger.error(msg)
            raise DeviceError(msg)

    def _setup_saving(self):
        """Ensure data is being saved while running and store prior state."""
        self._old_save_state = params.get_param(
            self._client, params.AsylumParam.SAVE_IMAGE)

        if not params.set_param(self._client,
                                params.AsylumParam.SAVE_IMAGE,
                                params.SAVE_ALL_IMAGES):
            msg = "Unable to set SaveImage to TRUE on startup."
            logger.error(msg)
            raise DeviceError(msg)

    def on_start_scan(self):
        """Override starting of scan."""
        success, __ = self._client.send_request(
            params.AsylumMethod.SCAN_FUNC,
            (params.AsylumMethod.START_SCAN_PARAM,))
        return (control_pb2.ControlResponse.REP_NO_RESPONSE if not success
                else control_pb2.ControlResponse.REP_SUCCESS)

    def on_stop_scan(self):
        """Override stopping of scan."""
        success, __ = self._client.send_request(
            params.AsylumMethod.SCAN_FUNC,
            (params.AsylumMethod.STOP_SCAN_PARAM,))
        return (control_pb2.ControlResponse.REP_NO_RESPONSE if not success
                else control_pb2.ControlResponse.REP_SUCCESS)

    def on_set_scan_params(self, scan_params: scan_pb2.ScanParameters2d
                           ) -> control_pb2.ControlResponse:
        """Override setting of scan params."""
        scan_size = scan_params.spatial.roi.size.y
        scan_y_ratio = 1.0
        scan_x_ratio = scan_params.spatial.roi.size.x / scan_size

        attrs = self.SCAN_PARAMS
        vals = (scan_params.spatial.roi.top_left.x,
                scan_params.spatial.roi.top_left.y,
                scan_size, scan_x_ratio, scan_y_ratio,
                scan_params.data.shape.x, scan_params.data.shape.y)
        attr_units = (scan_params.spatial.units, scan_params.spatial.units,
                      scan_params.spatial.units, None, None, None, None)
        # None means default of PHYS_UNITS
        asylum_units = (None, None, None, None, None, None, None)

        if params.set_param_list(self._client, attrs, vals, attr_units,
                                 asylum_units):
            return control_pb2.ControlResponse.REP_SUCCESS
        return control_pb2.ControlResponse.REP_PARAM_ERROR

    def on_set_zctrl_params(self, zctrl_params: feedback_pb2.ZCtrlParameters
                            ) -> control_pb2.ControlResponse:
        """Override setting zctrl."""
        desired_units = (None, None)
        attrs = self.ZCTRL_PARAMS
        vals = (zctrl_params.proportionalGain, zctrl_params.integralGain)
        if params.set_param_list(self._client, attrs, vals, desired_units,
                                 desired_units):
            return control_pb2.ControlResponse.REP_SUCCESS
        return control_pb2.ControlResponse.REP_PARAM_ERROR

    def poll_scan_state(self) -> scan_pb2.ScanState:
        """Override scan state polling."""
        scan_status = params.get_param(self._client,
                                       params.AsylumParam.SCAN_STATUS)

        if scan_status > 0:  # If 0, not scanning
            return scan_pb2.ScanState.SS_SCANNING
        else:
            return scan_pb2.ScanState.SS_FREE

    def poll_scan_params(self) -> scan_pb2.ScanParameters2d:
        """Override polling of scan params."""
        vals = params.get_param_list(self._client, self.SCAN_PARAMS)
        scan_params = scan_pb2.ScanParameters2d()
        scan_params.spatial.roi.top_left.x = vals[0]
        scan_params.spatial.roi.top_left.y = vals[1]

        scan_size = vals[2]
        scan_ratio_w = vals[3]
        scan_ratio_h = vals[4]

        scan_params.spatial.roi.size.x = scan_size * scan_ratio_w
        scan_params.spatial.roi.size.y = scan_size * scan_ratio_h
        scan_params.spatial.units = params.PHYS_UNITS

        # Asylum values returned as float, must convert to int?
        scan_params.data.shape.x = int(vals[5])
        scan_params.data.shape.y = int(vals[6])
        # Note setting data units, as these are linked to scan channel

        return scan_params

    def poll_scans(self) -> [scan_pb2.Scan2d]:
        """Override polling of scans."""
        val = params.get_param(self._client, params.AsylumParam.IMG_PATH)
        img_path = convert_igor_path_to_python_path(val)
        images = sorted(glob.glob(img_path + os.sep + "*" + self.IMG_EXT),
                        key=os.path.getmtime)  # Sorted by access time
        scan_path = images[-1] if images else None  # Get latest

        if (scan_path and not self._old_scan_path or
                scan_path != self._old_scan_path):
            self._old_scan_path = scan_path
            datasets = None
            try:
                logger.debug(f"Getting datasets from {scan_path} (each dataset"
                             " is a channel).")
                reader = sr.IgorIBWReader(scan_path)
                datasets = reader.read(verbose=False)
            except Exception as exc:
                logger.error(f"Failure loading scan at {scan_path}: {exc}")
                return self._old_scans

            if datasets:
                scans = []
                ts = get_file_modification_datetime(scan_path)
                for ds in datasets:
                    scan = conv.convert_sidpy_to_scan_pb2(ds)
                    scan.timestamp.FromDatetime(ts)
                    scan.filename = scan_path
                    scans.append(scan)
                self._old_scans = scans
                return scans

        return self._old_scans

    def poll_zctrl_params(self) -> feedback_pb2.ZCtrlParameters:
        """Override polling of zctrl."""
        vals = params.get_param_list(self._client, self.ZCTRL_PARAMS)
        zctrl_params = feedback_pb2.ZCtrlParameters()
        zctrl_params.feedbackOn = False  # TODO: how to read this!?!?!
        zctrl_params.proportionalGain = vals[0]
        zctrl_params.integralGain = vals[1]
        return zctrl_params
