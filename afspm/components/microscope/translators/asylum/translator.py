"""Handles device communication with aslyum research controllers."""

import os
import logging
import glob
import traceback
import SciFiReaders as sr
import sidpy
import numpy as np

from afspm.components.microscope.params import (ParameterHandler,
                                                ParameterNotSupportedError,
                                                ParameterError)
from afspm.components.microscope.actions import (ActionHandler,
                                                 MicroscopeAction)
from afspm.components.microscope.translator import (
    get_file_modification_datetime, MicroscopeError)
from afspm.components.microscope.config_translator import ConfigTranslator

from afspm.components.microscope.translators.asylum.client import XopClient
from afspm.components.microscope.translators.asylum.xop import (
    convert_igor_path_to_python_path)

from afspm.utils import array_converters as conv
from afspm.io.protos.generated import scan_pb2
from afspm.io.protos.generated import spec_pb2
from afspm.io.protos.generated import control_pb2


from . import params
from . import actions


logger = logging.getLogger(__name__)


# Attributes from the read scan file (differs from params.AsylumParameter, which
# contains UUIDs for getting/setting parameters).
SCAN_ATTRIB_ANGLE = 'ScanAngle'


# Default filenames for actions and params config files.
ACTIONS_FILENAME = 'actions.toml'
PARAMS_FILENAME = 'params.toml'


class AsylumTranslator(ConfigTranslator):
    """Handles device communication with the asylum controller.

    The AsylumTranslator communicates with the Asylum Research software via the
    XopClient, which sends/receives JSON messages over a zmq interface as
    defined by the Allen Institute's ZeroMQ-XOP project:
    https://github.com/AllenInstitute/ZeroMQ-XOP

    Attributes:
        _old_scan_path: the prior scan filepath. We use this to avoid loading
            the same scans multiple times.
        _old_spec_path: the prior spec filepath. We use this to avoid loading
            the same spectroscopies multiple times.
        _old_save_state: the prior state of whether or not we were saving
            scans.
        _old_last_scan: the prior state of whether or not we were scanning 1x
            per request.
        _old_last_spec: the prior state of whether or not we were spec'ing 1x
            per request.
        _save_spec_probe_pos: ProbePosition of XY position when last spec was
            done. Needed in order to create Spec1d from saved file, as the
            metadata (oddly) does not appear to store the XY position.
    """

    IMG_EXT = '.ibw'
    SCAN_PREFIX = 'Image'
    SPEC_PREFIX = 'Force'

    def __init__(self, param_handler: ParameterHandler = None,
                 action_handler: ActionHandler = None,
                 xop_client: XopClient = None,
                 **kwargs):
        """Init things, ensure we can hook into XOP Client.

        Args:
            param_handler: ParamHandler to use. If None, spawns default.
            action_handler: ActionHandler to use. If None, spawns default.
            xop_client: the xop client, used to intialize the ParamHandler
                and ActionHandler if these were not provided. If None,
                we spawn a default.
        """
        self._old_scan_path = None
        self._old_scans = []
        self._old_spec_path = None
        self._old_spec = None

        self._save_spec_probe_pos = None

        self._old_save_state = None
        self._old_last_scan = None
        # Default initialization of handler
        kwargs = self._init_handlers(xop_client, param_handler, action_handler,
                                     **kwargs)
        super().__init__(**kwargs)

        # Do some setup
        self._setup_probe_pos()
        self._set_save_params(save_state=params.ASYLUM_TRUE,
                              last_scan=params.ASYLUM_TRUE,
                              store_old_vals=True)

    def __del__(self):
        """Handle object destruction: reset what we changed on startup."""
        if self._old_save_state and self.old_last_scan:
            self._set_save_params(save_state=self._old_save_state,
                                  last_scan=self._old_last_scan,
                                  store_old_vals=False)

    def _init_handlers(self, client: XopClient,
                       param_handler: ParameterHandler,
                       action_handler: ActionHandler,
                       **kwargs) -> dict:
        """Init handlers and update kwargs."""
        if not client:
            client = XopClient()
        if not param_handler:
            param_handler = _init_param_handler(client)
            kwargs['param_handler'] = param_handler
        if not action_handler:
            action_handler = _init_action_handler(client)
            kwargs['action_handler'] = action_handler
        return kwargs

    def _setup_probe_pos(self):
        """Set up probe positioning so we can use it.

        The probe position logic is stored in Asylum via 3 WAVES:
        - root:Packages:MFP3D:Force:SpotX: 1D array of x-dimension positions
            where the probe could be moved to (in scan CS).
        - root:Packages:MFP3D:Force:SpotY: 1D array of y-dimension positions
            where the probe could be moved to (in scan CS).
        - root:Packages:MFP3D:Force:SpotNum: 0-indexed index of which
            spot we are using.

        By default, the arrays have only 1 value, and this value is in the
        middle of the scan CS.

        This method makes these arrays have 2 values, with the 2nd value being
        where we want it to run scans.
        """
        self.param_handler._call_method(params.INIT_POS_METHOD)

    def _set_save_params(self, save_state: int, last_scan: int,
                         store_old_vals: bool):
        """Set the save state and save 'mode' of the controller.

        Args:
            save_state: whether or not to save images as we scan.
            last_scan: whether or not we are scanning one image (2), or
                running continuously (0).
            store_old_vals: whether or not to store the old vals in
                self.old_save_state and old_save_mode, respectively. useful
                for resetting later.
        """
        if store_old_vals:
            self._old_save_state = self.param_handler.get_param(
                params.AsylumParam.SAVE_IMAGE.name)
            self._old_last_scan = self.param_handler.get_param(
                params.AsylumParam.LAST_SCAN.name)

        try:
            self.param_handler.set_param(params.AsylumParam.SAVE_IMAGE,
                                         save_state)
        except Exception:
            msg = f"Unable to set SaveImage to {save_state}."
            logger.error(msg)
            raise MicroscopeError(msg)

        try:
            self.param_handler.set_param(params.AsylumParam.LAST_SCAN,
                                         last_scan)
        except Exception:
            msg = f"Unable to set LastScan to {last_scan}."
            logger.error(msg)
            raise MicroscopeError(msg)

    def on_set_scan_params(self, scan_params: scan_pb2.ScanParameters2d
                           ) -> control_pb2.ControlResponse:
        """Override setting of scan params.

        We must set the scan params in a different order from default.
        """
        vals = [scan_params.spatial.roi.top_left.x,
                scan_params.spatial.roi.top_left.y,
                scan_params.spatial.roi.size.y,
                scan_params.spatial.roi.size.x,
                scan_params.data.shape.x,
                scan_params.data.shape.y,
                scan_params.spatial.roi.angle]
        attr_units = [scan_params.spatial.length_units,
                      scan_params.spatial.length_units,
                      scan_params.spatial.length_units,
                      scan_params.spatial.length_units,
                      scan_params.data.units,
                      scan_params.data.units,
                      scan_params.spatial.angular_units]

        try:
            self.param_handler.set_param_list(params.SCAN_PARAMS, vals, attr_units)
        except ParameterNotSupportedError:
            return control_pb2.ControlResponse.REP_PARAM_NOT_SUPPORTED
        except ParameterError:
            return control_pb2.ControlResponse.REP_PARAM_ERROR
        return control_pb2.ControlResponse.REP_SUCCESS

    def on_set_probe_pos(self, probe_position: spec_pb2.ProbePosition
                         ) -> control_pb2.ControlResponse:
        """Override setting of probe pos.

        We call the parent method, but *after* explicitly tell the
        controller to mvoe the probe to that position. This is to match
        our expected behavior, where the probe position setting causes
        it to move too.
        """
        super().on_set_probe_pos(probe_position)
        self.param_handler._call_method(params.MOVE_POS_METHOD)
        # TODO: Try replacing with actions:MOVE_PROBE_UUID?

    def poll_scope_state(self) -> scan_pb2.ScopeState:
        """Override scope state polling."""
        val = self.param_handler._call_method(params.GET_STATUS_METHOD)
        if params.ScopeState.SCANNING in val:
            return scan_pb2.ScopeState.SS_SCANNING
        elif params.ScopeState.SPEC in val:
            return scan_pb2.ScopeState.SS_SPEC
        elif params.ScopeState.MOVING in val:
            return scan_pb2.ScopeState.SS_MOVING

    def _get_latest_file(self, prefix: str) -> str | None:
        val = self.param_handler.get_param(params.AsylumParam.IMG_PATH.name)
        img_path = convert_igor_path_to_python_path(val)
        images = sorted(glob.glob(img_path + os.sep + prefix + "*"
                                  + self.IMG_EXT),
                        key=os.path.getmtime)  # Sorted by access time
        return images[-1] if images else None  # Get latest

    def poll_scans(self) -> [scan_pb2.Scan2d]:
        """Override polling of scans."""
        scan_path = self._get_latest_file(self.SCAN_PREFIX)
        if (scan_path and not self._old_scan_path or
                scan_path != self._old_scan_path):
            self._old_scan_path = scan_path
            datasets = None
            try:
                logger.debug(f"Getting datasets from {scan_path} (each dataset"
                             " is a channel).")
                reader = sr.IgorIBWReader(scan_path)
                datasets = list(reader.read(verbose=False).values())
            except Exception as exc:
                logger.error(f"Failure loading scan at {scan_path}: {exc}")
                return self._old_scans

            if datasets:
                scans = []
                ts = get_file_modification_datetime(scan_path)
                for ds in datasets:
                    scan = conv.convert_sidpy_to_scan_pb2(ds)

                    # Set ROI angle, timestamp, file
                    scan.params.spatial.roi.angle = ds.original_metadata[
                        SCAN_ATTRIB_ANGLE]
                    scan.timestamp.FromDatetime(ts)
                    scan.filename = scan_path
                    scans.append(scan)
                self._old_scans = scans
        return self._old_scans

    def poll_spec(self) -> spec_pb2.Spec1d:
        """Override spec polling."""
        spec_path = self._get_latest_file(self.SPEC_PREFIX)

        if spec_path and spec_path != self.old_spec_path:
            spec = self._load_spec(spec_path)
            if spec:
                self.old_spec_path = spec_path
                self.old_spec = spec
        return self.old_spec

    def _load_spec(self, fname: str) -> spec_pb2.Spec1d | None:
        """Load Spec1d from provided filename (None on failure)."""
        ts = get_file_modification_datetime(fname)
        try:
            reader = sr.IgorIBWReader(fname)
            ds_dict = reader.read(verbose=False)

            spec = convert_sidpy_to_spec_pb2(ds_dict, self._save_spec_probe_pos)
            spec.timestamp.FromDatetime(ts)
            spec.filename = fname
            return spec
        except Exception:
            logger.error(f'Could not read spec fname {fname}.'
                         'Got error.')
            logger.error(traceback.format_exc())
            return None

    def on_action_request(self, action: control_pb2.ActionMsg
                          ) -> control_pb2.ControlResponse:
        """Override action request.

        We need to switch the 'base name' of our save files before calling
        scans and specs. This is because these are, by default, saved with
        the same basename. That will get easily very confusing.

        We change the base name on these cases, but otherwise call the
        parent method.

        Note: for spec, we also store the probe position before running.
        This is because the metadata of the saved spec file does not contain
        this data.
        """
        if action.action == MicroscopeAction.START_SCAN:
            self.param_handler._call_method(params.SET_BASENAME_METHOD,
                                            (self.SCAN_PREFIX,))
        elif action.action == MicroscopeAction.START_SPEC:
            self.param_handler._call_method(params.SET_BASENAME_METHOD,
                                            (self.SPEC_PREFIX,))
            self._save_spec_probe_pos = self.poll_probe_pos()
        return super().on_action_request(action)


def _init_action_handler(client: XopClient) -> actions.AsylumActionHandler:
    """Initialize Asylum action handler pointing to defulat config."""
    actions_config_path = os.path.join(os.path.dirname(__file__),
                                       ACTIONS_FILENAME)
    return actions.AsylumActionHandler(actions_config_path, client)


def _init_param_handler(client: XopClient) -> params.AsylumParameterHandler:
    """Initialize Asylum action handler pointing to defulat config."""
    params_config_path = os.path.join(os.path.dirname(__file__),
                                      PARAMS_FILENAME)
    return params.AsylumParameterHandler(params_config_path, client)


def convert_sidpy_to_spec_pb2(ds_dict: dict[str, sidpy.Dataset],
                              probe_pos: spec_pb2.ProbePosition
                              ) -> spec_pb2.Spec1d:
    """Convert a dict of sidpy datasets to a single Spec1d.

    Bizarrely, the XY position of the spectrum is *not* stored in the
    metadata! So, we receive this as input (and store it in the translator
    before the spec collection is called).
    """
    names = [ds.dim_0.name for ds in ds_dict.values()]
    units = [ds.dim_0.units for ds in ds_dict.values()]
    num_variables = len(ds_dict)
    data_per_variable = list(ds_dict.values())[0].shape[0]

    # Extract data (first as 2D list)
    values = [ds.compute() for ds in ds_dict.values()]
    # Now, unravel to 1D version (using numpy's ravel)
    values = np.array(values).ravel().tolist()

    spec_data = spec_pb2.SpecData(num_variables=num_variables,
                                  data_per_variable=data_per_variable,
                                  names=names, units=units,
                                  values=values)
    spec = spec_pb2.Spec1d(position=probe_pos,
                           data=spec_data)
    return spec
