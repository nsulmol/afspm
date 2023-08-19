"""Holds sample components for testing purposes."""

import time
import copy
import threading
import pytest
import zmq

from google.protobuf.message import Message
from google.protobuf.timestamp_pb2 import Timestamp

from afspm.components.device_controller import DeviceController
from afspm.components.afspm_controller import AfspmController
from afspm.components.afspm_component import AfspmComponent


from afspm.io.pubsub.subscriber import Subscriber
from afspm.io.pubsub.publisher import Publisher
from afspm.io.pubsub.pubsubcache import PubSubCache
from afspm.io.cache import cache_logic as cl
from afspm.io.cache import pbc_logic as pbc


from afspm.io.control.control_server import ControlServer
from afspm.io.control.control_router import ControlRouter
from afspm.io.control.control_client import ControlClient, AdminControlClient

from afspm.io.protos.generated import scan_pb2
from afspm.io.protos.generated import control_pb2

# --- Device Controller Stuff --- #
class SampleDeviceController(DeviceController):
    start_ts = None
    tmp_scan_state = scan_pb2.ScanState.SS_FREE
    tmp_scan_params = scan_pb2.ScanParameters2d()
    tmp_scan = scan_pb2.Scan2d()

    def set_params(self, scan_time_ms, move_time_ms):
        self.scan_time_ms = scan_time_ms
        self.move_time_ms = move_time_ms

    def on_start_scan(self):
        self.start_ts = time.time()
        self.tmp_scan_state = scan_pb2.ScanState.SS_SCANNING
        return control_pb2.ControlResponse.REP_SUCCESS

    def on_stop_scan(self):
        self.start_ts = None
        self.tmp_scan_state = scan_pb2.ScanState.SS_FREE
        return control_pb2.ControlResponse.REP_SUCCESS

    def on_set_scan_params(self, scan_params: scan_pb2.ScanParameters2d
                           ) -> control_pb2.ControlResponse:
        self.start_ts = time.time()
        self.tmp_scan_state = scan_pb2.ScanState.SS_MOVING
        self.tmp_scan_params = scan_params
        return control_pb2.ControlResponse.REP_SUCCESS

    def poll_scan_state(self) -> scan_pb2.ScanState:
        """Add simulated scan time and move time."""
        #print("poll_scan_state start")
        if self.start_ts:
            duration = None
            update_scan = False
            if self.tmp_scan_state == scan_pb2.ScanState.SS_SCANNING:
                #print("waiting for scan to end")
                duration = self.scan_time_ms
                update_scan = True
            elif self.tmp_scan_state == scan_pb2.ScanState.SS_MOVING:
                #print("waiting for move to end")  # TODO: Delete prints!
                duration = self.move_time_ms

            if duration:
                curr_ts = time.time()
                #print(f"curr_ts - self.start_ts: {curr_ts - self.start_ts}, duration: {duration}")
                if curr_ts - self.start_ts > (duration / 1000):
                    self.start_ts = None
                    self.tmp_scan_state = scan_pb2.ScanState.SS_FREE
                    if update_scan:
                        #ts = Timestamp()
                        #ts.GetCurrentTime()
                        self.tmp_scan.timestamp.GetCurrentTime()
                        #print(f"Updated scan, ts: {self.tmp_scan.timestamp}")
        return self.tmp_scan_state

    def poll_scan_params(self) -> scan_pb2.ScanParameters2d:
        return copy.deepcopy(self.tmp_scan_params)

    def get_latest_scan(self) -> scan_pb2.Scan2d:
        return copy.deepcopy(self.tmp_scan)


def device_controller_routine(pub_url, server_url, psc_url, poll_timeout_ms,
                              loop_sleep_s,
                              hb_period_s, ctx, move_time_ms, scan_time_ms,
                              cache_kwargs):
    pub = Publisher(pub_url, cl.CacheLogic.create_envelope_from_proto)
    server = ControlServer(server_url, ctx)
    topics_none = []
    sub = Subscriber(
        psc_url, cl.extract_proto, topics_none,
        cl.update_cache, ctx,
        extract_proto_kwargs=cache_kwargs,
        update_cache_kwargs=cache_kwargs)

    devcon = SampleDeviceController(pub, server, poll_timeout_ms,
                                    loop_sleep_s, hb_period_s, ctx, sub)
    devcon.set_params(scan_time_ms, move_time_ms)
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
                      cl.CacheLogic.create_envelope_from_proto,
                      cl.update_cache, ctx,
                      extract_proto_kwargs=cache_kwargs,
                      update_cache_kwargs=cache_kwargs)
    router = ControlRouter(server_url, router_url, ctx, poll_timeout_ms)

    controller = AfspmController(loop_sleep_s, hb_period_s,
                                 psc, router, poll_timeout_ms,
                                 ctx)
    controller.run()

    # Forcing closure of bound sockets (for pytests)
    psc.backend.close()
    router.frontend.close()
