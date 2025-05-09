"""Holds sample components for testing purposes."""

import time
import logging
from typing import Optional, Any

from afspm.utils.units import convert

from afspm.components.microscope.translator import MicroscopeTranslator
from afspm.components.microscope.map_translator import MapTranslator
from afspm.components.microscope.params import (MicroscopeParameter,
                                                ParameterError)
from afspm.components.microscope.actions import MicroscopeAction
from afspm.components.microscope.scheduler import MicroscopeScheduler
from afspm.components.drift.scheduler import CSCorrectedScheduler

from afspm.io import common
from afspm.io.pubsub.subscriber import Subscriber
from afspm.io.pubsub.publisher import Publisher
from afspm.io.pubsub.cache import PubSubCache
from afspm.io.pubsub.logic import cache_logic as cl


from afspm.io.control.server import ControlServer
from afspm.io.control.router import ControlRouter

from afspm.io.protos.generated import scan_pb2
from afspm.io.protos.generated import control_pb2
from afspm.io.protos.generated import feedback_pb2
from afspm.io.protos.generated import spec_pb2


logger = logging.getLogger(__name__)


# ----- 'Action' handling methods ----- #
def on_start_scan(translator: MicroscopeTranslator):
    """Handle START_SCAN action."""
    translator.start_ts = time.time()
    translator.dev_scope_state = scan_pb2.ScopeState.SS_SCANNING
    return control_pb2.ControlResponse.REP_SUCCESS


def on_stop_scan(translator: MicroscopeTranslator):
    """Handle STOP_SCAN action."""
    translator.start_ts = None
    translator.dev_scope_state = scan_pb2.ScopeState.SS_FREE
    return control_pb2.ControlResponse.REP_SUCCESS


def on_start_spec(translator: MicroscopeTranslator):
    """Handle START_SPEC action."""
    translator.start_ts = time.time()
    translator.dev_scope_state = scan_pb2.ScopeState.SS_SPEC
    return control_pb2.ControlResponse.REP_SUCCESS


def on_stop_spec(translator: MicroscopeTranslator):
    """Handle STOP_SPEC action."""
    translator.start_ts = None
    translator.dev_scope_state = scan_pb2.ScopeState.SS_FREE
    return control_pb2.ControlResponse.REP_SUCCESS


# ----- Parameter request methods ----- #
def handle_scan_speed(ctrlr: MicroscopeTranslator,
                      val: Optional[Any] = None,
                      units: Optional[str] = None,
                      ) -> (Any, str):
    """Get/set scan speed.

    Arguments:
        ctrl: a reference to the MicroscopeTranslator.
        val: if not None, the value we should set.
        units: if not None, the units of the provided value.
    Returns:
        - the value at end of the method.
        - the units of value.
    """
    if val:
        if not units:
            return (control_pb2.ControlResponse.REP_PARAM_ERROR, None, None)
        ctrlr.scan_speed = convert(float(val), units, ctrlr.ss_units)
    return (ctrlr.scan_speed, ctrlr.ss_units)


def fail_on_moving_speed(ctrlr: MicroscopeTranslator,
                         val: Optional[Any] = None,
                         units: Optional[str] = None
                         ) -> (Any, str):
    """Get/set with failure case.

    This simulates a 'supported' param which fails on setting.
    We simply want to ensure the failure is passed on.

    Arguments:
        ctrlr: a reference to the MicroscopeTranslator
        val: if not None, the value we should set.
        units: if not None, the units of the provided value.

    Returns:
        - the value of at end of the method.
        - the units of the value.
    """
    if val:
        raise ParameterError
    else:
        return (25, ctrlr.vib_ampl_units)


# --- Microscope Translator Stuff --- #
class SampleMicroscopeTranslator(MapTranslator):
    def __init__(self, scan_time_s, move_time_s, spec_time_s,
                 **kwargs):
        self.scan_speed = 500
        self.ss_units = 'nm/s'

        self.vib_ampl_units = '%'

        self.start_ts = None
        self.dev_scope_state = scan_pb2.ScopeState.SS_FREE
        self.dev_scan_params = scan_pb2.ScanParameters2d()
        self.dev_probe_pos = spec_pb2.ProbePosition()
        self.dev_scan = None
        self.dev_spec = None

        self.scan_time_s = scan_time_s
        self.move_time_s = move_time_s
        self.spec_time_s = spec_time_s
        kwargs['name'] = 'dev_con'
        super().__init__(**kwargs)

    def _build_param_method_map(self):
        self.param_method_map = {
            MicroscopeParameter.SCAN_SPEED: handle_scan_speed,
            MicroscopeParameter.MOVING_SPEED: fail_on_moving_speed}

    def _build_action_method_map(self):
        self.action_method_map = {
            MicroscopeAction.START_SCAN: on_start_scan,
            MicroscopeAction.STOP_SCAN: on_stop_scan,
            MicroscopeAction.START_SPEC: on_start_spec,
            MicroscopeAction.STOP_SPEC: on_stop_spec,
        }

    def on_set_scan_params(self, scan_params: scan_pb2.ScanParameters2d
                           ) -> control_pb2.ControlResponse:
        self.start_ts = time.time()
        self.dev_scope_state = scan_pb2.ScopeState.SS_MOVING
        self.dev_scan_params = scan_params
        return control_pb2.ControlResponse.REP_SUCCESS

    def on_set_zctrl_params(self, zctrl_params: feedback_pb2.ZCtrlParameters
                            ) -> control_pb2.ControlResponse:
        return control_pb2.ControlResponse.REP_CMD_NOT_SUPPORTED

    def on_set_probe_pos(self, probe_pos: spec_pb2.ProbePosition):
        self.dev_probe_pos = probe_pos
        return control_pb2.ControlResponse.REP_SUCCESS

    def poll_scope_state(self) -> scan_pb2.ScopeState:
        return self.dev_scope_state

    def poll_scan_params(self) -> scan_pb2.ScanParameters2d:
        return self.dev_scan_params

    def poll_scans(self) -> list[scan_pb2.Scan2d]:
        return [self.dev_scan] if self.dev_scan else []

    def poll_spec(self) -> spec_pb2.Spec1d:
        return self.dev_spec

    def poll_zctrl_params(self) -> feedback_pb2.ZCtrlParameters:
        return feedback_pb2.ZCtrlParameters()

    def poll_probe_pos(self) -> spec_pb2.ProbePosition:
        return self.dev_probe_pos

    def run_per_loop(self):
        if self.start_ts:
            duration = None
            update_scan = False
            update_spec = False
            if self.dev_scope_state == scan_pb2.ScopeState.SS_SCANNING:
                duration = self.scan_time_s
                update_scan = True
            elif self.dev_scope_state == scan_pb2.ScopeState.SS_MOVING:
                duration = self.move_time_s
            elif self.dev_scope_state == scan_pb2.ScopeState.SS_SPEC:
                duration = self.spec_time_s
                update_spec = True

            if duration:
                curr_ts = time.time()
                if curr_ts - self.start_ts > duration:
                    logger.debug("Enough time has passed, changing state "
                                 "from %s to free.",
                                 common.get_enum_str(scan_pb2.ScopeState,
                                                     self.dev_scope_state))
                    self.start_ts = None
                    self.dev_scope_state = scan_pb2.ScopeState.SS_FREE
                    if update_scan:
                        self.dev_scan = scan_pb2.Scan2d()
                        self.dev_scan.timestamp.GetCurrentTime()
                    if update_spec:
                        self.dev_spec = spec_pb2.Spec1d()
                        self.dev_spec.timestamp.GetCurrentTime()
        super().run_per_loop()


def microscope_translator_routine(pub_url, server_url, psc_url,
                                  ctx, move_time_ms, scan_time_ms,
                                  spec_time_ms, cache_kwargs):
    pub = Publisher(pub_url, cl.CacheLogic.get_envelope_for_proto)
    server = ControlServer(server_url, ctx)
    topics_none = []
    sub = Subscriber(
        psc_url, cl.extract_proto, topics_none,
        cl.update_cache, ctx,
        extract_proto_kwargs=cache_kwargs,
        update_cache_kwargs=cache_kwargs)

    translator = SampleMicroscopeTranslator(scan_time_ms / 1000,
                                            move_time_ms / 1000,
                                            spec_time_ms / 1000,
                                            publisher=pub,
                                            control_server=server,
                                            ctx=ctx, subscriber=sub)
    translator.run()

    # Forcing closure of bound sockets (for pytests)
    pub._publisher.close()
    server._server.close()


# --- MicroscopeScheduler Stuff --- #
def microscope_scheduler_routine(psc_url, pub_url, server_url, router_url,
                                 cache_kwargs, ctx):
    psc = PubSubCache(psc_url, pub_url,
                      cl.extract_proto,
                      cl.CacheLogic.get_envelope_for_proto,
                      cl.update_cache, ctx,
                      extract_proto_kwargs=cache_kwargs,
                      update_cache_kwargs=cache_kwargs)
    router = ControlRouter(server_url, router_url, ctx)

    scheduler = MicroscopeScheduler('scheduler', psc, router, ctx=ctx)
    scheduler.run()

    # Forcing closure of bound sockets (for pytests)
    psc._backend.close()
    router._frontend.close()


def cs_corrected_scheduler_routine(psc_url, pub_url, server_url, router_url,
                                   cache_kwargs, ctx):
    psc = PubSubCache(psc_url, pub_url,
                      cl.extract_proto,
                      cl.CacheLogic.get_envelope_for_proto,
                      cl.update_cache, ctx,
                      extract_proto_kwargs=cache_kwargs,
                      update_cache_kwargs=cache_kwargs)
    router = ControlRouter(server_url, router_url, ctx)

    scheduler = CSCorrectedScheduler(name='scheduler',
                                     pubsubcache=psc,
                                     router=router, ctx=ctx)
    scheduler.run()

    # Forcing closure of bound sockets (for pytests)
    psc._backend.close()
    router._frontend.close()
