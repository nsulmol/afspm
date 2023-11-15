"""Contains methods faking a tip issue."""

import logging
from dataclasses import dataclass

from google.protobuf.message import Message

from afspm.spawn import LOGGER_ROOT
from afspm.components.component import AfspmComponent

from afspm.io.protos.generated import scan_pb2
from afspm.io.protos.generated import control_pb2


logger = logging.getLogger(LOGGER_ROOT + '.samples.point_subscan.' + __name__)


# ----- Fake Tip Analysis Methods ----- #
@dataclass
class TipStateData:
    scan_period_raise_problem: int
    scan_count: int = 0
    problem_resolved: bool = True


def on_message_received(component: AfspmComponent, envelope: str,
                        proto: Message, tip_state: TipStateData):
    """Throw a tip problem every N scans (but wait until resolved)."""
    tip_problem = control_pb2.ExperimentProblem.EP_TIP_SHAPE_CHANGED
    if tip_state.problem_resolved:
        if isinstance(proto, scan_pb2.ScanStateMsg):
            tip_state.scan_count += 1
        if tip_state.scan_count > tip_state.scan_period_raise_problem:
            rep = component.control_client.add_experiment_problem(tip_problem)
            if rep == control_pb2.ControlResponse.REP_SUCCESS:
                tip_state.problem_resolved = False
                tip_state.scan_count = 0
            else:
                logger.warning('Unable to add tip problem.')

    elif (isinstance(proto, control_pb2.ControlState) and
          tip_problem not in proto.problems_set):
        tip_state.problem_resolved = True
