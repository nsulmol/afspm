"""Reruns scans if drift was too much."""

import logging

from google.protobuf.message import Message

from ..scan import handler
from ...io.pubsub.logic import pbc_logic
from ...io import common

from ...io.protos.generated import control_pb2
from ...io.protos.generated import scan_pb2


logger = logging.getLogger(__name__)


class DriftRescanner(handler.ScanningComponent):
    """Monitors for scans to rerun, doing so with EP_THERMAL_DRIFT flagging.

    This component is expected to receive ScanParameters2d from a
    CSCorrectedScheduler indicating scans that need to be redone (due to there
    being too little overlap between the desired and actual scan areas). When
    it does, it:
    1. Flags an EP_THERMAL_DRIFT problem.
    2. Takes control.
    3. Requests a rescan of the scan(s).
    4. Once the scan(s) have been redone, it removes the flagged problem.

    Note that, as it uses ScanHandler logic, the publisher must also send
    ControlState and ScopeState messages.

    We expect this component's subscriber to be listening to a url that
    CSCorrectedScheduler publishes to.

    Attributes:
        scan_params_id: cache key for ScanParameters2d.
        control_state_id: cache key for ControlState.
        scope_state_id: cache key for ScopeState.
    """

    EXP_PROBLEM = control_pb2.ExperimentProblem.EP_THERMAL_DRIFT
    SCAN_PARAMS_ID = pbc_logic.ProtoBasedCacheLogic.get_envelope_for_proto(
        scan_pb2.ScanParameters2d())
    CONTROL_STATE_ID = pbc_logic.ProtoBasedCacheLogic.get_envelope_for_proto(
        control_pb2.ControlState())
    SCOPE_STATE_ID = pbc_logic.ProtoBasedCacheLogic.get_envelope_for_proto(
        scan_pb2.ScopeStateMsg())

    def __init__(self, rerun_wait_s: int,
                 scan_params_id: str = SCAN_PARAMS_ID,
                 control_state_id: str = CONTROL_STATE_ID,
                 scope_state_id: str = SCOPE_STATE_ID,
                 **kwargs):
        """Init our Drift Rescanner."""
        self.scan_params_id = scan_params_id
        self.control_state_id = control_state_id
        self.scope_state_id = scope_state_id
        next_params_kwargs = {}
        next_params_kwargs['component'] = self
        super().__init__(rerun_wait_s, rerun_scans, next_params_kwargs,
                         self.EXP_PROBLEM, **kwargs)

    def on_message_received(self, envelope: str, proto: Message):
        """Override to run ScanHandler."""
        super().on_message_received(envelope, proto)
        self._handle_rescans()

    def _handle_rescans(self):
        if self.unable_to_run():
            return

        sp_key = self.scan_params_id
        cs_key = self.control_state_id
        ss_key = self.scope_state_id

        if (len(self.subscriber.cache[sp_key]) == 0 and
                len(self.subscriber.cache[cs_key]) > 0):
            # If we have gone through all scan_params in cache and the
            # control state still has EXP_PROBLEM, try to remove it.
            in_control = (self.subscriber.cache[cs_key][-1].client_in_control_id
                          == self.name)
            finishing_scan = (self.scan_handler._desired_scope_state ==
                              scan_pb2.ScopeState.SS_SCANNING and
                              self.subscriber.cache[ss_key][-1].scope_state ==
                              scan_pb2.ScopeState.SS_FREE)
            if in_control and finishing_scan:
                logger.warning('Releasing control following rescan.')
                rep = self.control_client.remove_experiment_problem(
                    self.EXP_PROBLEM)
                if rep != control_pb2.ControlResponse.REP_SUCCESS:
                    logger.trace('Failed to remove exp problem.')
        elif (len(self.subscriber.cache[sp_key]) > 0 and
                len(self.subscriber.cache[cs_key]) > 0):
            # If we have scan_params in cache and we have *not* logged
            # our EXP_PROBLEM, do so.
            problems_set = self.subscriber.cache[cs_key][-1].problems_set
            if (not common.is_problem_in_problems_set(self.EXP_PROBLEM,
                                                      problems_set)):
                logger.warning('Taking control to rescan.')
                rep = self.control_client.add_experiment_problem(
                    self.EXP_PROBLEM)
                if rep != control_pb2.ControlResponse.REP_SUCCESS:
                    logger.trace('Failed to add exp problem.')

    def unable_to_run(self):
        """Tells us whether or not we can run."""
        cannot_run = (self.scan_params_id not in self.subscriber.cache or
                      self.control_state_id not in self.subscriber.cache or
                      self.control_client is None)
        return cannot_run


def rerun_scans(component: DriftRescanner
                ) -> (scan_pb2.ScanParameters2d | None):
    """Rerun scans in DriftRescanner's self.scan_params_to_rerun."""
    if component.unable_to_run():
        logger.trace("ScanParameters2d have yet to arrive in cache.")
        return None

    key = component.scan_params_id
    if len(component.subscriber.cache[key]) > 0:
        scan_params = component.subscriber.cache[key].pop()
        return scan_params
    return None
