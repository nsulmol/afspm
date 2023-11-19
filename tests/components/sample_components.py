"""Holds sample components for testing purposes."""

import time
import logging

from afspm.components.device.controller import DeviceController
from afspm.components.device.params import DeviceParameter
from afspm.components.afspm.controller import AfspmController

from afspm.io.pubsub.subscriber import Subscriber
from afspm.io.pubsub.publisher import Publisher
from afspm.io.pubsub.cache import PubSubCache
from afspm.io.pubsub.logic import cache_logic as cl


from afspm.io.control.server import ControlServer
from afspm.io.control.router import ControlRouter

from afspm.io.protos.generated import scan_pb2
from afspm.io.protos.generated import control_pb2


logger = logging.getLogger(__name__)


# --- Device Controller Stuff --- #
class SampleDeviceController(DeviceController):

    def __init__(self, scan_time_s, move_time_s, **kwargs):
        self.operating_mode = 'AM-AFM'
        self.start_ts = None
        self.dev_scan_state = scan_pb2.ScanState.SS_FREE
        self.dev_scan_params = scan_pb2.ScanParameters2d()
        self.dev_scan = None

        self.scan_time_s = scan_time_s
        self.move_time_s = move_time_s
        kwargs['name'] = 'dev_con'
        super().__init__(**kwargs)

        self.param_method_map = {DeviceParameter.OPERATING_MODE:
                                 self.handle_operating_mode,
                                 DeviceParameter.TIP_VIBRATING_AMPL:
                                 self.fail_on_vib_ampl}

    def on_start_scan(self):
        self.start_ts = time.time()
        self.dev_scan_state = scan_pb2.ScanState.SS_SCANNING
        return control_pb2.ControlResponse.REP_SUCCESS

    def on_stop_scan(self):
        self.start_ts = None
        self.dev_scan_state = scan_pb2.ScanState.SS_FREE
        return control_pb2.ControlResponse.REP_SUCCESS

    def on_set_scan_params(self, scan_params: scan_pb2.ScanParameters2d
                           ) -> control_pb2.ControlResponse:
        self.start_ts = time.time()
        self.dev_scan_state = scan_pb2.ScanState.SS_MOVING
        self.dev_scan_params = scan_params
        return control_pb2.ControlResponse.REP_SUCCESS

    def poll_scan_state(self) -> scan_pb2.ScanState:
        return self.dev_scan_state

    def poll_scan_params(self) -> scan_pb2.ScanParameters2d:
        return self.dev_scan_params

    def poll_scans(self) -> list[scan_pb2.Scan2d]:
        return [self.dev_scan] if self.dev_scan else []

    def handle_operating_mode(self, set_value: str = None
                              ) -> (control_pb2.ControlResponse, str):
        """Get/set operating mode.

        Arguments:
            set_value: if not None, the value we should set
                our operating mode to (as a str).
        Returns:
            - success/failure of operation
            - the value of operating mode at the end of the method.
        """
        if set_value:
            self.operating_mode = set_value
        return (control_pb2.ControlResponse.REP_SUCCESS, self.operating_mode)


    def fail_on_vib_ampl(self, set_value: str = None
                         ) -> (control_pb2.ControlResponse, str):
        """Get/set with failure case.

        This simulates a 'supported' param which fails on setting.
        We simply want to ensure the failure is passed on.

        Arguments:
            set_value: if not None, the value we should set our operating
                mode to (as a str).
        Returns:
            - success/failure of operation
            - the value of operating mode at the end of the method. On failure,
            return set_value.
        """
        if set_value:
            return (control_pb2.ControlResponse.REP_PARAM_ERROR, set_value)
        else:
            return (control_pb2.ControlResponse.REP_SUCCESS, "25")

    def run_per_loop(self):
        if self.start_ts:
            duration = None
            update_scan = False
            if self.dev_scan_state == scan_pb2.ScanState.SS_SCANNING:
                duration = self.scan_time_s
                update_scan = True
            elif self.dev_scan_state == scan_pb2.ScanState.SS_MOVING:
                duration = self.move_time_s

            if duration:
                curr_ts = time.time()
                if curr_ts - self.start_ts > duration:
                    logger.debug("Enough time has passed, changing state "
                                 "from %s to free.",
                                 common.get_enum_str(scan_pb2.ScanState,
                                                     self.dev_scan_state))
                    self.start_ts = None
                    self.dev_scan_state = scan_pb2.ScanState.SS_FREE
                    if update_scan:
                        self.dev_scan = scan_pb2.Scan2d()
                        self.dev_scan.timestamp.GetCurrentTime()
        super().run_per_loop()


def device_controller_routine(pub_url, server_url, psc_url,
                              ctx, move_time_ms, scan_time_ms,
                              cache_kwargs):
    pub = Publisher(pub_url, cl.CacheLogic.get_envelope_for_proto)
    server = ControlServer(server_url, ctx)
    topics_none = []
    sub = Subscriber(
        psc_url, cl.extract_proto, topics_none,
        cl.update_cache, ctx,
        extract_proto_kwargs=cache_kwargs,
        update_cache_kwargs=cache_kwargs)

    devcon = SampleDeviceController(scan_time_ms / 1000, move_time_ms / 1000,
                                    publisher=pub, control_server=server,
                                    ctx=ctx, subscriber=sub)
    devcon.run()

    # Forcing closure of bound sockets (for pytests)
    pub._publisher.close()
    server._server.close()



from afspm.io import common


# --- AfspmController Stuff --- #
def afspm_controller_routine(psc_url, pub_url, server_url, router_url,
                             cache_kwargs, ctx):
    psc = PubSubCache(psc_url, pub_url,
                      cl.extract_proto,
                      cl.CacheLogic.get_envelope_for_proto,
                      cl.update_cache, ctx,
                      extract_proto_kwargs=cache_kwargs,
                      update_cache_kwargs=cache_kwargs)
    router = ControlRouter(server_url, router_url, ctx)

    controller = AfspmController('afspm_ctrl', psc, router, ctx=ctx)
    controller.run()

    # Forcing closure of bound sockets (for pytests)
    psc._backend.close()
    router._frontend.close()
