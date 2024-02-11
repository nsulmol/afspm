"""Handles device communication with aslyum research controllers."""

import os
import logging
import glob
from typing import Any

from afspm.components.device.controller import (DeviceController,
                                                get_file_modification_datetime)
from afspm.components.device.params import ParameterError

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

    SCAN_PARAMS = (params.AsylumParameter.TL_X, params.AsylumParameter.TL_Y,
                   params.AsylumParameter.SCAN_SIZE,
                   params.AsylumParameter.SCAN_X_RATIO,
                   params.AsylumParameter.SCAN_Y_RATIO,
                   params.AsylumParameter.RES_X, params.AsylumParameter.RES_Y)

    ZCTRL_PARAMS = (params.AsylumParameter.CP,
                    params.AsylumParameter.CI)

    IMG_EXT = ".ibw"

    def __init__(self, xop_client: XopClient, **kwargs):
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
        # Reset save state!
        if not params.set_param(self._client,
                                params.AsylumParameter.SAVE_IMAGE,
                                self._old_save_state):
            msg = "Was unable to reset SaveImage state on closure!"
            logger.error(msg)
            raise ParameterError(msg)

    def _setup_saving(self):
        """Ensure data is being saved while running and store prior state."""
        self._old_save_state = params.get_param(
            self._client, params.AsylumParameter.SAVE_IMAGE)
        _handle_params_error(self._old_save_state, "Unable to store SaveImage!")

        if not params.set_param(self._client,
                                params.AsylumParameter.SAVE_IMAGE,
                                params.SAVE_ALL_IMAGES):
            msg = "Unable to set SaveImage to TRUE on startup."
            logger.error(msg)
            raise ParameterError(msg)

    def on_start_scan(self):
        success, __ = self._client.send_request(
            params.AsylumMethod.SCAN_FUNC,
            (params.AsylumParameter.START_SCAN_PARAM))
        return (control_pb2.ControlResponse.REP_NO_RESPONSE if not success
                else control_pb2.ControlResponse.REP_SUCCESS)

    def on_stop_scan(self):
        success, __ = self._client.send_request(
            params.AsylumMethod.SCAN_FUNC,
            (params.AsylumParameter.STOP_SCAN_PARAM))
        return (control_pb2.ControlResponse.REP_NO_RESPONSE if not success
                else control_pb2.ControlResponse.REP_SUCCESS)

    def on_set_scan_params(self, scan_params: scan_pb2.ScanParameters2d
                           ) -> control_pb2.ControlResponse:
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
        desired_units = (None, None)
        attrs = self.ZCTRL_PARAMS
        vals = (zctrl_params.proportionalGain, zctrl_params.integralGain)
        if params.set_param_list(self._client, attrs, vals, desired_units,
                                 desired_units):
            return control_pb2.ControlResponse.REP_SUCCESS
        return control_pb2.ControlResponse.REP_PARAM_ERROR

    def poll_scan_state(self) -> scan_pb2.ScanState:
        # Poll for current scan state and send out!
        scan_status = params.get_param(self._client,
                                       params.AsylumParameter.SCAN_STATUS)
        _handle_params_error(scan_status, "Polling for scan state failed!")

        if scan_status:  # If 0, not scanning
            return scan_pb2.ScanState.SS_SCANNING
        else:
            return scan_pb2.ScanState.SS_FREE

    def poll_scan_params(self) -> scan_pb2.ScanParameters2d:
        vals = params.get_param_list(self._client, self.SCAN_PARAMS)
        _handle_params_error(vals, "Polling for scan params failed!")

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
        val = params.get_param(self._client, params.AsylumParameter.IMG_PATH)
        _handle_params_error(val, "Requesting img path failed!")

        img_path = convert_igor_path_to_python_path(val)
        images = sorted(glob.glob(img_path + os.sep + "*" + IMG_EXT),
                        key=os.path.getmtime)  # Sorted by access time
        scan_path = images[0]

        if (scan_path and not self._old_scan_path or
                scan_path != self._old_scan_path):
            self._old_scan_path = scan_path
            datasets = None
            try:
                logger.debug("Getting datasets from %s (each dataset is a "
                             "channel)", scan_path)
                reader = sr.IgorIBWReader(scan_path)
                datasets = reader.read(verbose=False)
            except Exception as exc:
                logger.error("Failure loading scan at %s.", scan_path)
                return self._old_scans

            if datasets:
                scans = []
                for ds in datasets:
                    scan = conv.convert_sidpy_to_scan_pb2(ds)
                    ts = get_file_modification_datetime(fname)
                    scan.timestamp.FromDatetime(ts)
                    scans.append(scan)
                self._old_scans = scans
                return scans

        return self._old_scans

    def poll_zctrl_params(self) -> feedback_pb2.ZCtrlParameters:
        vals = params.get_param_list(self._client, self.ZCTRL_PARAMS)
        _handle_params_error(vals, "Polling for CP/CI failed!")

        zctrl_params = feedback_pb2.ZCtrlParameters()
        zctrl_params.feedbackOn = False  # TODO: how to read this!?!?!
        zctrl_params.proportionalGain = vals[0]
        zctrl_params.integralGain = vals[1]
        return zctrl_params


def _handle_params_error(vals: Any | None, msg: str):
    """Raise error if vals is None, passing msg."""
    if vals is None:
        logger.error(msg)
        raise ParameterError(msg)
