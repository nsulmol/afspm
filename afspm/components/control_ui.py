"""PySimpleGUI interface for controllling the MicroscopeScheduler."""

import copy
import logging
import tkinter as tk

from google.protobuf.message import Message

from ..io import common
from ..io.control import client as ctrl_client

from .component import AfspmComponentUI
from ..io.protos.generated import control_pb2
from ..io.protos.generated import scan_pb2


logger = logging.getLogger(__name__)


AFSPM_CTRL = 'AFSPM Control'
CTRL_MODE = 'Current ControlMode:'
IN_CTRL = 'In Control By:'
SCOPE_STATE = 'Scope State:'
PROBLEMS_SET = 'Problem Set:'
FLUSH_PROBLEMS_SET = 'Flush Problem Set'
END_EXP = 'End Experiment'
ERROR_LOG = 'Last Error Log:'

IN_CTRL_KEY = 'CTRL'
SCOPE_STATE_KEY = 'SCOPE_STATE'
PROBLEMS_SET_KEY = 'PRBLM'
FLUSH_PROBLEMS_KEY = 'FLUSH'
ERROR_LOG_KEY = 'ERROR_LOG'
END_EXP_KEY = 'END_EXP'

BUTTON_ID = 'button'
LABEL_ID = 'label'

MODE_GROUP = 'Modes'

TOPICS_TO_SUB_KEY = 'topics_to_sub'
ALL_TOPICS = ""


class AfspmControlUI(AfspmComponentUI):
    """Simple UI class to present info from, and control, the scheduler.

    This class will present a simple UI to show current ControlState/ScopeState
    of the afspm system, and allow admin controls of it.

    Note that this component *requires*:
    - An AdminControlClient to be provided, so it can use admin controls.
    - A Subscriber subscribed to control_pb2.ControlState and
    scan_pb2.ScopeStateMsg (alternatively, subscribed to all).

    Without this, it will not function properly! We avoided overloading the
    constructor to enforce this, as it would create a ton of input arguments
    (and break from the current standard of providing already-instantiated
    I/O constituents).

    Attributes:
        control_state: holds the latest ControlState received.
        scope_state: holds the latest ScopeState received.
        labels: dictionary of the various labels we will be modifying.
        buttons: dictionary of the various buttons we may modify.
        frame: top-level tkinter Frame.
        cm_frame: tkinter Frame instance associated with our RadioButton list
            of ControlModes.
    """

    def __init__(self, **kwargs):
        """Initialize our control UI."""
        self.control_state = control_pb2.ControlState()
        self.scope_state = scan_pb2.ScopeState.SS_UNDEFINED

        super().__init__(**kwargs)

        # Handle user selecting to close the window
        self.root.protocol("WM_DELETE_WINDOW", self._on_closing)

        if not isinstance(self.control_client, ctrl_client.AdminControlClient):
            msg = "AdminControlClient not provided, cannot continue. Closing."
            logger.error(msg)
            raise AssertionError(msg)
        # TODO: Think of way to validate the subscriber is subscribed to
        # expected states...

    def _create_ui(self):
        self.root.title(AFSPM_CTRL)
        pack_kwargs = {'fill': tk.X}

        self.labels = {}
        self.buttons = {}

        # Top-level frame
        self.frame = tk.Frame(self.root)
        self.frame.pack(fill=tk.BOTH, expand=True)

        subframe = tk.Frame(self.frame)
        subframe.pack(**pack_kwargs)
        tk.Label(subframe, text=CTRL_MODE).pack(fill=tk.X, side=tk.LEFT)

        # Set up control mode radio buttons
        cm = control_pb2.ControlMode
        control_modes = [common.get_enum_str(cm, mode) for mode in
                         [cm.CM_MANUAL, cm.CM_AUTOMATED, cm.CM_PROBLEM]]

        self.cm_frame = tk.Frame(self.frame)
        self.cm_frame.pack(**pack_kwargs)

        self.cm_variable = tk.StringVar(self.root, control_modes[0])
        for mode, col in zip(control_modes, range(0, 3)):
            button = tk.Radiobutton(self.cm_frame, text=mode,
                                    variable=self.cm_variable,
                                    value=mode,
                                    command=self._control_mode_selected)
            self.buttons[mode] = button
            button.grid(row=0, column=col, sticky=tk.NSEW)
            self.cm_frame.grid_rowconfigure(0, weight=1)
            self.cm_frame.grid_columnconfigure(col, weight=1)

        # Set up 'state' rows
        labels_and_text_to_make = {IN_CTRL_KEY: (IN_CTRL, 'None'),
                                   SCOPE_STATE_KEY: (SCOPE_STATE, ''),
                                   PROBLEMS_SET_KEY: (PROBLEMS_SET, '')}

        for key, val in labels_and_text_to_make.items():
            subframe = tk.Frame(self.frame)
            subframe.pack(**pack_kwargs)

            label_text = val[0]
            default_state_text = val[1]

            # Using pack and side left/right
            tk.Label(subframe, text=label_text).pack(side=tk.LEFT)
            self.labels[key] = tk.Label(subframe, text=default_state_text)
            self.labels[key].pack(side=tk.RIGHT)

        # Set up buttons
        subframe = tk.Frame(self.frame)
        subframe.pack(**pack_kwargs)

        self.buttons[PROBLEMS_SET_KEY] = tk.Button(
            subframe, text=FLUSH_PROBLEMS_SET, command=self._on_flush_problems)
        self.buttons[PROBLEMS_SET_KEY].grid(row=0, column=0, sticky=tk.NSEW)
        self.buttons[END_EXP_KEY] = tk.Button(
            subframe, text=END_EXP, command=self._on_end_experiment)
        self.buttons[END_EXP_KEY].grid(row=0, column=1, sticky=tk.NSEW)
        subframe.grid_rowconfigure(0, weight=1)
        subframe.grid_columnconfigure(0, weight=1)
        subframe.grid_columnconfigure(1, weight=1)

        # Set up error logging
        subframe = tk.Frame(self.frame)
        subframe.pack(**pack_kwargs)

        tk.Label(subframe, text=ERROR_LOG).pack(side=tk.LEFT, **pack_kwargs)
        self.labels[ERROR_LOG_KEY] = tk.Label(subframe, text='')
        self.labels[ERROR_LOG_KEY].pack(**pack_kwargs)

    def _control_mode_selected(self):
        mode_str = self.cm_variable.get()
        mode = common.get_enum_val(control_pb2.ControlMode, mode_str)
        logger.info(f"Control Mode Selected: {mode_str}")
        self._send_req(self.control_client.set_control_mode,
                       mode)

    def _on_flush_problems(self):
        logger.info("Flush problems set selected.")
        problems = copy.deepcopy(self.control_state.problems_set)
        for problem in problems:
            self._send_req(self.control_client.remove_experiment_problem,
                           problem)

    def _on_end_experiment(self):
        logger.info("End experiment selected.")
        self._send_req(self.control_client.end_experiment, None)

    def _on_closing(self):
        logger.info("UI closure clicked, exiting.")
        self.heartbeater.handle_closing()
        self.destroy()

    def _send_req(self, req, arg):
        rep = req(arg) if arg is not None else req()
        if rep != control_pb2.ControlResponse.REP_SUCCESS:
            msg = (f"MicroscopeTranslator refused request {req.__name__}, "
                   f"returned {rep}")
            logger.warning(msg)
            self.labels[ERROR_LOG_KEY].config(text=msg)

    def on_message_received(self, envelope: str, proto: Message):
        """Check what has changed and update UI accordingly."""
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
        elif isinstance(proto, scan_pb2.ScopeStateMsg):
            last_state = copy.deepcopy(self.scope_state)
            self.scope_state = proto.scope_state
            if self.scope_state != last_state:
                self._handle_scope_state_changed()

    def _handle_mode_changed(self):
        ctrl_mode = control_pb2.ControlMode

        mode = self.control_state.control_mode
        mode_str = common.get_enum_str(ctrl_mode, mode)
        self.cm_variable.set(mode_str)
        self._update_radio_buttons()

    def _handle_client_changed(self):
        client = self.control_state.client_in_control_id
        client = client if client != "" else "None"
        self.labels[IN_CTRL_KEY].config(text=client)

    def _handle_scope_state_changed(self):
        txt = common.get_enum_str(scan_pb2.ScopeState, self.scope_state)
        self.labels[SCOPE_STATE_KEY].config(text=txt)

    def _handle_problems_changed(self):
        problems_set = self.control_state.problems_set
        log_txt = ""
        for problem in problems_set:
            log_txt += common.get_enum_str(control_pb2.ExperimentProblem,
                                           problem) + '\n'
        self.labels[PROBLEMS_SET_KEY].config(text=log_txt)
        self._update_radio_buttons()

    def _update_radio_buttons(self):
        """Set disabled/enabled state of radio options."""
        ctrl_mode = control_pb2.ControlMode

        problem_id = common.get_enum_str(ctrl_mode, ctrl_mode.CM_PROBLEM)
        auto_id = common.get_enum_str(ctrl_mode, ctrl_mode.CM_AUTOMATED)

        problems_logged = len(self.control_state.problems_set) != 0
        problem_state = tk.DISABLED if not problems_logged else tk.NORMAL
        self.buttons[problem_id].config(state=problem_state)
        auto_state = tk.DISABLED if problems_logged else tk.NORMAL
        self.buttons[auto_id].config(state=auto_state)
