"""Handles device communication with aslyum research controllers."""

import os
import logging
import glob
import SciFiReaders as sr
import sidpy
import numpy as np

from ...params import (ParameterHandler, ParameterNotSupportedError,
                       ParameterError, MicroscopeParameter,
                       DEFAULT_PARAMS_FILENAME)
from ...actions import (ActionHandler, MicroscopeAction, ActionError,
                        DEFAULT_ACTIONS_FILENAME)
from ...translator import get_file_modification_datetime
from ... import config_translator as ct

from .....utils import array_converters as conv
from .....io.protos.generated import scan_pb2
from .....io.protos.generated import spec_pb2
from .....io.protos.generated import control_pb2

from .client import XopClient
from .xop import convert_igor_path_to_python_path
from . import params
from . import actions


logger = logging.getLogger(__name__)


# The Asylum controller does not appear to have a great float tolerance (at
# least for probe position).
FLOAT_TOLERANCE = 1e-05
FLOAT_TOLERANCE_KEY = 'float_tolerance'


# Attributes from the read scan file (differs from params.AsylumParameter,
# which contains UUIDs for getting/setting parameters).
SCAN_ATTRIB_ANGLE = 'ScanAngle'


class AsylumTranslator(ct.ConfigTranslator):
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
        _old_saving_mode: the prior SavingMode state.
        _old_scanning_mode: the prior state of whether or not we were scanning
            1x per request.
        _save_spec_probe_pos: ProbePosition of XY position when last spec was
            done. Needed in order to create Spec1d from saved file, as the
            metadata (oddly) does not appear to store the XY position.
    """

    DEFAULT_SPAWN_DELAY_S = 5.0  # Slow startup.
    DEFAULT_BEAT_PERIOD_S = 7.5  # Slow to respond.

    IMG_EXT = '.ibw'
    SCAN_PREFIX = 'Image'
    SPEC_PREFIX = 'Force'

    # NOTE: We need our own order of scan params, because we are calling it
    # in a different order (we need to due to internals).
    SCAN_PARAMS = [MicroscopeParameter.SCAN_TOP_LEFT_X,
                   MicroscopeParameter.SCAN_TOP_LEFT_Y,
                   MicroscopeParameter.SCAN_SIZE_Y,  # <-- this is different
                   MicroscopeParameter.SCAN_SIZE_X,
                   MicroscopeParameter.SCAN_RESOLUTION_X,
                   MicroscopeParameter.SCAN_RESOLUTION_Y,
                   MicroscopeParameter.SCAN_ANGLE]

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

        self._old_saving_mode = None
        self._old_scanning_mode = None
        # Default initialization of handler
        kwargs = self._init_handlers(xop_client, param_handler, action_handler,
                                     **kwargs)

        # Set hard-coded float tolerance if not provided
        if FLOAT_TOLERANCE_KEY not in kwargs:
            kwargs[FLOAT_TOLERANCE_KEY] = FLOAT_TOLERANCE

        # Tell parent class that Asylum *does not* detect moving
        kwargs[ct.DETECTS_MOVING_KEY] = False
        super().__init__(**kwargs)

        # Do some setup
        self._setup_probe_pos()
        self._set_save_params(saving_mode=params.SavingMode.SAVE.value,
                              scanning_mode=params.ScanningMode.ONE_FRAME.value,
                              store_old_vals=True)

    def _init_handlers(self, client: XopClient,
                       param_handler: ParameterHandler,
                       action_handler: ActionHandler,
                       **kwargs) -> dict:
        """Init handlers and update kwargs."""
        if not client:
            client = XopClient()
        if not param_handler:
            param_handler = _init_param_handler(client)
            kwargs[ct.PARAM_HANDLER_KEY] = param_handler
        if not action_handler:
            action_handler = _init_action_handler(client)
            kwargs[ct.ACTION_HANDLER_KEY] = action_handler
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
        try:
            self.param_handler._call_method(params.INIT_POS_METHOD)
        except ParameterError as e:
            logger.error('First call to ZeroMQ-XOP failed. Ensure you have '
                         'followed the setup instructions!')
            raise e

    def _set_save_params(self, saving_mode: int, scanning_mode: int,
                         store_old_vals: bool):
        """Set the saving mdoe and scanning mode of the controller.

        Args:
            saving_mode: whether or not to save images as we scan. See
                params.SavingMode.
            scanning_mode: whether or not we are scanning one image (2), or
                running continuously (0). See params.ScanningMode.
            store_old_vals: whether or not to store the old vals in
                self._old_saving_mode and _old_scanning_mode, respectively.
                useful for resetting later.
        """
        if store_old_vals:
            self._old_saving_mode = self.param_handler.get_param(
                params.AsylumParam.SAVING_MODE.name)
            self._old_scanning_mode = self.param_handler.get_param(
                params.AsylumParam.SCANNING_MODE.name)

        self.param_handler.set_param(params.AsylumParam.SAVING_MODE.name,
                                     saving_mode)
        self.param_handler.set_param(params.AsylumParam.SCANNING_MODE.name,
                                     scanning_mode)

    def on_set_scan_params(self, scan_params: scan_pb2.ScanParameters2d
                           ) -> control_pb2.ControlResponse:
        """Override setting of scan params.

        We must set the scan params in a different order from default.

        As a reminder: we do not send data units, because the translator
        has no concept of the 'units' of data at the 'scan parameter' level.
        When we read saved scans or specs, we are able to retrieve their
        data units, but this is not something we concern ourselves with at
        the MicroscopeTranslator granularity.
        """
        vals = [scan_params.spatial.roi.top_left.x,
                scan_params.spatial.roi.top_left.y,
                scan_params.spatial.roi.size.y,  # <-- this is different
                scan_params.spatial.roi.size.x,
                scan_params.data.shape.x,
                scan_params.data.shape.y,
                scan_params.spatial.roi.angle]
        attr_units = [scan_params.spatial.length_units,
                      scan_params.spatial.length_units,
                      scan_params.spatial.length_units,
                      scan_params.spatial.length_units,
                      None, None,
                      scan_params.spatial.angular_units]

        try:
            self.param_handler.set_param_list(self.SCAN_PARAMS, vals,
                                              attr_units)
            if not self.detects_moving:  # Send fake SS_MOVING if needed
                self._handle_sending_fake_move()
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
        try:
            self.action_handler.request_action(actions.MOVE_PROBE_UUID)
        except ActionError:
            return control_pb2.ControlResponse.REP_ACTION_ERROR
        return control_pb2.ControlResponse.REP_SUCCESS

    def poll_scope_state(self) -> scan_pb2.ScopeState:
        """Override scope state polling."""
        val = self.param_handler._call_method(params.GET_STATUS_METHOD)
        # Bit comparisons
        if params.ScopeState.SCANNING.value & val:
            return scan_pb2.ScopeState.SS_SCANNING
        elif params.ScopeState.SINGLE_SPEC.value & val:
            return scan_pb2.ScopeState.SS_SPEC
        return scan_pb2.SS_FREE

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

                    # BUG WORKAROUND: scifireaders does not properly read the
                    # length units of scans (it puts the data_units). Because
                    # of this, we get a conversion error when dealing, e.g.
                    # with the phase channel (it tries to convert 'm' to
                    # 'deg').
                    # Until this is fixed, we are just hard-coding the
                    # length_units as 'm', which is what IBW files appear
                    # to be anyway.
                    scan.params.spatial.length_units = 'm'

                    # Set ROI angle, timestamp, file
                    scan.params.spatial.roi.angle = ds.original_metadata[
                        SCAN_ATTRIB_ANGLE]
                    angle_unit = self.param_handler.get_unit(
                        MicroscopeParameter.SCAN_ANGLE)
                    scan.params.spatial.angular_units = angle_unit

                    scan.timestamp.FromDatetime(ts)
                    scan.filename = scan_path
                    scans.append(scan)
                self._old_scans = scans
        return self._old_scans

    def poll_spec(self) -> spec_pb2.Spec1d:
        """Override spec polling."""
        spec_path = self._get_latest_file(self.SPEC_PREFIX)

        if (spec_path and not self._old_spec_path or
                spec_path != self._old_spec_path):
            spec = self._load_spec(spec_path)
            if spec:
                self._old_spec_path = spec_path
                self._old_spec = spec
        return self._old_spec

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
                         'Got error.', exc_info=True)
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
                                       DEFAULT_ACTIONS_FILENAME)
    return actions.AsylumActionHandler(actions_config_path, client)


def _init_param_handler(client: XopClient) -> params.AsylumParameterHandler:
    """Initialize Asylum action handler pointing to defulat config."""
    params_config_path = os.path.join(os.path.dirname(__file__),
                                      DEFAULT_PARAMS_FILENAME)
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
