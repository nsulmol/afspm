"""Fake tip detector, which throws an exception after a # of images."""

import logging

from google.protobuf.message import Message

from afspm.utils.log import LOGGER_ROOT
from afspm.components.component import AfspmComponent
from afspm.io.protos.generated import scan_pb2
from afspm.io.protos.generated import control_pb2


logger = logging.getLogger(LOGGER_ROOT + '.samples.grid_subscan.' + __name__)


class FreqTriggerTipDetector(AfspmComponent):
    """Fake tip detector, will throw  tip problem after a preset # images.

    Attributes:
        scan_period_raise_problem: how often (in # of scans) before it will
            raise a tip issue.
        scan_count: current scan count.
        problem_resolved: whether or not the tip problem has been resolved.
    """

    def __init__(self, scan_period_raise_problem: int, **kwargs):
        self.scan_period_raise_problem = scan_period_raise_problem
        self.scan_count = 0
        self.problem_resolved = True
        super().__init__(**kwargs)

    def on_message_received(self, envelope: str, proto: Message):
        """Throw a tip problem every N scans (but wait until resolved)."""
        tip_problem = control_pb2.ExperimentProblem.EP_TIP_SHAPE_CHANGED
        if self.problem_resolved:
            if isinstance(proto, scan_pb2.ScopeStateMsg):
                self.scan_count += 1
            if self.scan_count > self.scan_period_raise_problem:
                rep = self.control_client.add_experiment_problem(tip_problem)
                if rep == control_pb2.ControlResponse.REP_SUCCESS:
                    self.problem_resolved = False
                    self.scan_count = 0
                else:
                    logger.warning('Unable to add tip problem.')

        elif (isinstance(proto, control_pb2.ControlState) and
              tip_problem not in proto.problems_set):
            self.problem_resolved = True
