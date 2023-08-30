"""PySimpleGUI interface for controllling the AfspmController."""

import copy
from types import MappingProxyType  # Immutable dict
import logging
import zmq
import PySimpleGUI as sg

from google.protobuf.message import Message

from ..io.pubsub import subscriber as sub
from ..io.control import control_client as ctrl_client

from .afspm_component import AfspmComponent
from ..io.protos.generated import control_pb2
from ..io.protos.generated import scan_pb2


logger = logging.getLogger(__name__)


AFSPM_CTRL = 'AFSPM Control'
CTRL_MODE = 'Current ControlMode:'
IN_CTRL = 'In Control By:'
SCAN_STATE = 'Scan State:'
PROBLEMS_SET = 'Problem Set:'
PROBLEMS_SET = 'Flush Problem Set'
END_EXP = 'End Experiment'
ERROR_LOG = 'Last Error Log:'

IN_CTRL_KEY = 'CTRL'
SCAN_STATE_KEY = 'SCAN_STATE'
PROBLEMS_SET_KEY = 'PRBLM'
ERROR_LOG_KEY = 'ERROR_LOG'

MODE_GROUP = 'Modes'

# TODO: Could *re-implement* all of these as string enums, and avoid this crap...
# Set up map modes
MAP_MODE_STR = MappingProxyType({
    control_pb2.ControlMode.CM_MANUAL: 'Manual',
    control_pb2.ControlMode.CM_AUTOMATED: 'Automated',
    control_pb2.ControlMode.CM_PROBLEM: 'Problem'
})

MAP_STR_MODE = {}
for mode, txt in MAP_MODE_STR.items():
    MAP_STR_MODE[txt] = mode

SCAN_STATE_STR = MappingProxyType({
    scan_pb2.ScanState.SS_UNDEFINED: 'Undefined',
    scan_pb2.ScanState.SS_MOVING: 'Moving',
    scan_pb2.ScanState.SS_SCANNING: 'Scanning',
    scan_pb2.ScanState.SS_FREE: 'Free'
})

PROBLEMS_SET_STR = MappingProxyType({
    control_pb2.ExperimentProblem.EP_NONE: 'None',
    control_pb2.ExperimentProblem.EP_TIP_SHAPE_CHANGED: 'Tip Shape Changed',
    control_pb2.ExperimentProblem.EP_DEVICE_MALFUNCTION: 'Device Malfunction'
})

TOPICS_TO_SUB_KEY = 'topics_to_sub'
ALL_TOPICS = ""


class AfspmControlUI(AfspmComponent):
    """Simple UI class to present info from, and control, the afspm controller.

    This class will present a simple UI to show current ControlState/ScanState
    of the afspm system, and allow admin controls of it.

    Note that this component *requires*:
    - An AdminControlClient to be provided, so it can use adminc controls.
    - A Subscriber subscribed to control_pb2.ControlState and
    scan_pb2.ScanStateMsg (alternatively, subscribed to all).

    Without this, it will not function properly! We avoided overloading the
    constructor to enforce this, as it would create a ton of input arguments
    (and break from the current standard of providing already-instantiated
    I/O constituents).

    Attributes:
        self.mode_buttons: a list of strings corresponding to the control mode
            buttons.
        self.map_mode_button_to_mode

    """

    def __init__(self, **kwargs):
        self._create_ui()
        self.control_state = control_pb2.ControlState()
        self.scan_state = scan_pb2.ScanState.SS_UNDEFINED
        super().__init__(**kwargs)

        if not isinstance(self.control_client, ctrl_client.AdminControlClient):
            msg = "AdminControlClient not provided, cannot continue. Closing."
            logger.error(msg)
            raise AssertionError(msg)
        # TODO: Think of way to validate the subscriber is subscribed to
        # expected states...

    def _create_ui(self):
        # Mode control
        self.layout = [[sg.Text(CTRL_MODE)]]

        buttons = []
        txt_automated = MAP_MODE_STR[control_pb2.CM_AUTOMATED]
        for mode, txt in MAP_MODE_STR.items():
            is_default = mode == txt_automated
            buttons.append(sg.Radio(txt, MODE_GROUP, key=txt,
                                    default=is_default))
        self.layout.append(buttons)

        # The rest
        self.layout.extend([[sg.Text(IN_CTRL)],
                            [sg.Text(key=IN_CTRL_KEY)],
                            [sg.Text(SCAN_STATE)],
                            [sg.Text(key=SCAN_STATE_KEY)],
                            [sg.Text(PROBLEMS_SET)],
                            [sg.Text(key=PROBLEMS_SET_KEY)],
                            [sg.Button(PROBLEMS_SET)],
                            [sg.Button(END_EXP)],
                            [sg.Text(ERROR_LOG)],
                            [sg.Text(key=ERROR_LOG_KEY)]])
        self.window = sg.Window(AFSPM_CTRL, self.layout, finalize=True)  #TODO finalize added...

    def _handle_ui_event_loop(self):
        #self.layout[ERROR_LOG_KEY].update(value="")  # Clear error log
        event, values = self.window.read(timeout=self.poll_timeout_ms)

        req_methods = []
        req_args = []
        if event in MAP_STR_MODE:
            logger.debug("Control Mode Selected: %s", MAP_STR_MODE[event])
            req_methods.append(self.control_client.set_control_mode)
            req_args.append(MAP_STR_MODE[event])
        elif event == PROBLEMS_SET:
            logger.debug("Flush problems set selected.")
            problems = copy.deepcopy(self.control_state.problems_set)
            for problem in problems:
                req_methods.append(self.control_client.
                                   remove_experiment_problem)
                req_args.append(problem)
        elif event == END_EXP:
            logger.debug("End experiment selected.")
            req_methods.append(self.control_client.end_experiment)
            req_args.append(None)
        elif event == sg.WINDOW_CLOSED:
            logger.info("UI closure clicked, exiting.")
            self.heartbeater.handle_closing()
            self.stay_alive = False

        if len(req_methods) > 0 and len(req_args) > 0:
            for req, arg in zip(req_methods, req_args):
                rep = req(arg) if arg is not None else req()
                if rep != control_pb2.ControlResponse.REP_SUCCESS:
                    msg = ("DeviceController refused request %s, returned %s"
                           % (req.__name__, rep))
                    logger.warning(msg)
                    self.window[ERROR_LOG_KEY].update(value=msg)

    def on_message_received(self, envelope: str, proto: Message):
        logger.debug("Message received: %s, %s", envelope, proto)
        if isinstance(proto, control_pb2.ControlState):
            last_cs = copy.deepcopy(self.control_state)
            self.control_state = proto
            if self.control_state.control_mode != last_cs.control_mode:
                self._handle_mode_changed()
            if (self.control_state.client_in_control_id !=
                    last_cs.client_in_control_id):
                self._handle_client_changed()
            if (self.control_state.problems_set != last_cs.problems_set):
                self._handle_problems_changed()
        elif isinstance(proto, scan_pb2.ScanStateMsg):
            last_state = copy.deepcopy(self.scan_state)
            self.scan_state = proto.scan_state
            if(self.scan_state != last_state):
                self._handle_scan_state_changed()

    def _handle_mode_changed(self):
        mode = self.control_state.control_mode
        button_id = MAP_MODE_STR[mode]
        self.window[button_id].update(value=True)

        # Set disabled/enabled state of radio options
        problem_id = MAP_MODE_STR[control_pb2.ControlMode.CM_PROBLEM]
        auto_id = MAP_MODE_STR[control_pb2.ControlMode.CM_AUTOMATED]

        problem_disabled = button_id != problem_id
        self.window[problem_id].update(disabled=problem_disabled)
        self.window[auto_id].update(disabled=not problem_disabled)

    def _handle_client_changed(self):
        client = self.control_state.client_in_control_id
        client = client if client != "" else "None"
        self.window[IN_CTRL_KEY].update(client)

    def _handle_scan_state_changed(self):
        self.window[SCAN_STATE_KEY].update(SCAN_STATE_STR[self.scan_state])

    def _handle_problems_changed(self):
        problems_set = self.control_state.problems_set
        log_txt = ""
        for problem in problems_set:
            log_txt += PROBLEMS_SET_STR[problem] + '\n'

        self.window[PROBLEMS_SET_KEY].update(log_txt)

    def run_per_loop(self):
        self._handle_ui_event_loop()
