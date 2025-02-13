"""Handles device communication with the Omicron SXM controller."""
"""TODO:
        naming: OmicronSXM
        title for doscstring
        nice strings for msg
        add space between # and comment
        implement changing scan speed
        how much info to give when raising MicroscopeError?
        be consistent between 'Omicron' and 'Anfatec'
        remove plus signs in string concatenation

    TODO later: refactor methods in params.py to cach and print exceptions,
    then return true or false for success.

    NOTE: maybe a bit of a design mistake: I originally had the setters return
    a bool indicating success/failure, but changed them to return None. So now
    instead of doing if set(): return pb.controlresponse.success, I have to 
    catch and print exceptions to detect failure. Might need to change it back
    to returning bools

    NOTE: We constantly reset the DDE object here, because of a found bug in
    SXMRemote. If/when fixed, consider cleaning this up!
"""

import logging

from afspm.components.microscope.translator import (
    MicroscopeTranslator, MicroscopeError, get_file_modification_datetime)
from afspm.utils.array_converters import convert_sidpy_to_scan_pb2

from afspm.components.microscope.translators.omicron.params import (
    OmicronParameter, OmicronParameterUnit, set_param, get_param,
    set_pb2_scan_params,    set_pb2_feedback_params, get_all_scan_params,
    get_all_feedback_params, PARAM_METHOD_MAP)
from afspm.components.microscope.translators.omicron.scan import (get_latest_scan_metadata_path)

from SciFiReaders.readers.microscopy.spm.afm import pifm

from afspm.io.protos.generated import scan_pb2
from afspm.io.protos.generated import control_pb2
from afspm.io.protos.generated import feedback_pb2

logger = logging.getLogger(__name__)

# The directory containing SXMRemote must be added to pythonpath of venv
# TODO do this ourselves with the poetry setup files?
# say this in readme
try:
    import SXMRemote
except ModuleNotFoundError as e:
    logger.error("SXMRemote not found, make sure to add it to your PythonPath:"
                 '\n\t Export PYHTONPATH = < PathToSXMRemote >:$PYTHONPATH"')
    raise e

class OmicronSXMTranslator(MicroscopeTranslator):
    """Handles device communication with the Omicron SXM controller

    Note: we encountered difficulties working with the methods provided by
    Anfatec to read the latest scan, so we request the directory where scans
    are saved via the constructor to find the latest one ourselves.
    """
    def __init__(self, save_directory: str = None, **kwargs):
        """Initialize internal logic.""" 

        self.last_scan_fname = ""   #which of these two do we want to compare?
        self.old_scans = []         #

        #omicron-specific field
        self.save_directory = save_directory

        self.DDE_client = SXMRemote.DDEClient("SXM","Remote")

        super().__init__(**kwargs)

        self.param_method_map = PARAM_METHOD_MAP   #TODO implement get_set_scan_speed()
    
    #TODO put this in own file to avoid circular bad when implementing get set
    def reset_DDE(self): 
        """Closes current DDE connexion (if any) and establishes a new one.
            
        Called before reading / setting scan parameters to avoid the "set+get
        error": setting and getting parameters (the same or different) sometimes
        causes an error in Anfatec's code.
        Note: if the current implementation does not work, try using the
        disconnect method of the DDE client (SXMRemote line 134).
        """
        try:
            #if self.DDE_client: self.DDE_client.__del__()   #is this required? or can we un-assign self.DDE_client and let the garbage collector clean it up?
            self.DDE_client = SXMRemote.DDEClient("SXM","Remote")   #NOTE: could be problem if DDE must be closed before making new one
        except Exception as e:
            msg = f"Error resetting DDE connection: {e}"
            logger.error(msg)
            raise Exception(msg)

    def on_start_scan(self) -> control_pb2.ControlResponse:
        """Override on starting scan."""
        self.reset_DDE()
        try: 
            set_param(self.DDE_client, "Scan", 1.0)
            return control_pb2.ControlResponse.REP_SUCCESS
        except Exception as e:
            print(e)
            return control_pb2.ControlResponse.REP_FAILURE

    def on_stop_scan(self) -> control_pb2.ControlResponse:
        """Handle a request to stop a scan."""
        self.reset_DDE()
        try:
            set_param(self.DDE_client, "Scan", 0.0)
            return control_pb2.ControlResponse.REP_SUCCESS
        except Exception as e:
            print(e)
            return control_pb2.ControlResponse.REP_FAILURE

    def on_set_scan_params(self, scan_params: scan_pb2.ScanParameters2d
                           ) -> control_pb2.ControlResponse:
        """Handle a request to change the scan parameters."""
        self.reset_DDE()
        try:
            set_pb2_scan_params(self.DDE_client, scan_params)
            return control_pb2.ControlResponse.REP_SUCCESS
        except Exception as e:
            print(e)
            return control_pb2.ControlResponse.REP_FAILURE

    def on_set_zctrl_params(self, zctrl_params: feedback_pb2.ZCtrlParameters
                            ) -> control_pb2.ControlResponse:
        """Handle a request to change the Z-Controller Feedback parameters."""
        self.reset_DDE()
        try:
            set_pb2_feedback_params(self.DDE_client, zctrl_params):
            return control_pb2.ControlResponse.REP_SUCCESS
        except Exception as e:
            print(e)
            return control_pb2.ControlResponse.REP_PARAM_ERROR

    def poll_scope_state(self) -> scan_pb2.ScopeState:
        """Poll the controller for the current scope state.

        NOTE: We cannot detect whether the motor is running via SXMRemote.
        Throws a MicroscopeError on failure.
        """
        self.reset_DDE()
        scanning = get_param(self.DDE_client, "Scan")
        if scanning: 
            return scan_pb2.ScopeState.SS_COLLECTING
        else:
            return scan_pb2.ScopeState.SS_FREE

    def poll_scan_params(self) -> scan_pb2.ScanParameters2d:
        """Poll the controller for the current scan parameters."""
        self.reset_DDE()

        vals = get_all_scan_params(self.DDE_client)
        scan_params = scan_pb2.ScanParameters2d()
        scan_params.spatial.roi.top_left.x = vals[0]
        scan_params.spatial.roi.top_left.y = vals[1]
        scan_params.spatial.roi.size.x = vals[2]
        scan_params.spatial.roi.size.y = vals[2]
        scan_params.spatial.length_units = OmicronParameterUnit.X
    
        # Note: we must provide image resolution as an int, so we convert here 
        scan_params.data.shape.x = int(vals[3])
        scan_params.data.shape.y = int(vals[3])

        return scan_params

    def poll_zctrl_params(self) -> feedback_pb2.ZCtrlParameters:
        """Poll the controller for the current Z-Control parameters."""
        self.reset_DDE()

        vals = get_all_feedback_params()    #TODO make also return whether zctrl is on
        feedback_params = feedback_pb2.ZCtrlParameters()
        feedback_params.feedbackOn = bool(vals[0])
        feedback_params.proportionalGain = vals[1]
        feedback_params.integralGain = vals[2]

        return feedback_params

    def poll_scans(self) -> list[scan_pb2.Scan2d]:
        """Obtain latest performed scans."""
        latest_path = get_latest_scan_metadata_path(self.save_directory)

        # Avoid reloading scans if same filename. Return old scans.
        if latest_path == self.old_scan_fname:
            return self.old_scans

        dt_modified = get_file_modification_datetime(latest)  #this is a datetime

        # TODO: Validate. From code, it would seem we need the path to the .int,
        # *not* the .txt metadata file...
        try:
            logger.debug(f'Getting datasets from {latest_path}.')
            reader = pifm.PiFMTranslator(latest_path)
            res = reader.read()     #returns a list of sidpy datasets
        except Exception as exc:
            logger.error(f"Failure loading scan at {latest_path}: {exc}")
            return self.old_scans

        # Convert and prepare scans, update old_scans.
        scans = []
        for dataset in res:
            scan = convert_sidpy_to_scan_pb2(dataset)

            # Set ROI angle, timestamp, filename
            # TODO: Set ROI Angle!
            scan.timestamp.FromDateTime(dt_modified)
            scan.filename = latest_path
            scans.append(scan)
        
        self.old_scans = scans
        self.last_scan_fname = latest_path  #path to metadata is OK?

        return  self.old_scans
