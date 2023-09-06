"""Holds sample components for testing purposes."""

import time
import logging

from afspm.components.device_controller import DeviceController
from afspm.components.afspm_controller import AfspmController

from afspm.io.pubsub.subscriber import Subscriber
from afspm.io.pubsub.publisher import Publisher
from afspm.io.pubsub.pubsubcache import PubSubCache
from afspm.io.cache import cache_logic as cl


from afspm.io.control.control_server import ControlServer
from afspm.io.control.control_router import ControlRouter

from afspm.io.protos.generated import scan_pb2
from afspm.io.protos.generated import control_pb2


logger = logging.getLogger(__name__)


# --- Device Controller Stuff --- #
class SampleDeviceController(DeviceController):

    def __init__(self, scan_time_s, move_time_s, **kwargs):
        self.start_ts = None
        self.dev_scan_state = scan_pb2.ScanState.SS_FREE
        self.dev_scan_params = scan_pb2.ScanParameters2d()
        self.dev_scan = scan_pb2.Scan2d()

        self.scan_time_s = scan_time_s
        self.move_time_s = move_time_s
        kwargs['name'] = 'dev_con'
        super().__init__(**kwargs)

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

    def poll_scan(self) -> scan_pb2.Scan2d:
        return self.dev_scan

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
                    self.start_ts = None
                    self.dev_scan_state = scan_pb2.ScanState.SS_FREE
                    if update_scan:

                        self.dev_scan.timestamp.GetCurrentTime()
        super().run_per_loop()


def device_controller_routine(pub_url, server_url, psc_url, poll_timeout_ms,
                              loop_sleep_s,
                              hb_period_s, ctx, move_time_ms, scan_time_ms,
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
                                    poll_timeout_ms=poll_timeout_ms,
                                    loop_sleep_s=loop_sleep_s,
                                    hb_period_s=hb_period_s, ctx=ctx,
                                    subscriber=sub)
    devcon.run()

    # Forcing closure of bound sockets (for pytests)
    pub.publisher.close()
    server.server.close()


# --- AfspmController Stuff --- #
def afspm_controller_routine(psc_url, pub_url, server_url, router_url,
                             cache_kwargs, loop_sleep_s, hb_period_s,
                             poll_timeout_ms, ctx):
    psc = PubSubCache(psc_url, pub_url,
                      cl.extract_proto,
                      cl.CacheLogic.get_envelope_for_proto,
                      cl.update_cache, ctx,
                      extract_proto_kwargs=cache_kwargs,
                      update_cache_kwargs=cache_kwargs)
    router = ControlRouter(server_url, router_url, ctx, poll_timeout_ms)

    controller = AfspmController('afspm_ctrl', loop_sleep_s, hb_period_s,
                                 psc, router, poll_timeout_ms,
                                 ctx)
    controller.run()

    # Forcing closure of bound sockets (for pytests)
    psc.backend.close()
    router.frontend.close()
