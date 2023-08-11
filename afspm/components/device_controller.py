"""Holds Abstract Device Controller Class (defines controller logic)."""

import time
import copy
from abc import ABCMeta, abstractmethod
from typing import Callable, MappingProxyType
import zmq
from google.protobuf.message import Message

from .io.pubsub import publisher as pub
from .io.control import commands as cmd
from .io.control import control_server

from .io.protos.generated import scan_pb2 as scan
from .io.protos.generated import control_pb2 as ctrl


class DeviceController(metaclass=ABCMeta):
    """Handles communicating with SPM device and handling requests.

    The DeviceController is the principal node for communicating with an SPM
    device (usually via an actual SPM controller). It is responsible for:
    - Receiving requests from a ControlClient and responding to them;
    - Sending appropriate requests to the device itself, to perform actions;
    - Monitoring the SPM device for state changes, and reporting these changes
    to any listeners via its publisher;
    - Sending out any performed scans out to listeners via its publisher.

    It communicates with any ControlClients via a zmq REP node, where it
    receives requests and handles them via its appropriate methods (e.g.
    on_start_scan()).

    It sends out state changes and scans via a zmq PUB node, where it publishes
    these aspects.

    This is an abstract class, as device communication is SPM controller
    specific. We expect a DeviceController child class for a given SPM
    controller.

    Attributes:
        publisher: Publisher instance, for publishing data.
        control_server: ControlServer instance, for responding to control
            requests.
        loop_sleep_ms: how long the device sleeps between loops in its main
            loop.
        scan_state: device's current ScanState.
        scan_params; device's current ScanParameters2d.
        scan: device's most recent Scan2d.
    """

    def __init__(self, ctrl_url: str, pub_url: str, loop_sleep_ms: int,
                 get_envelope_given_proto: Callable[[Message], str],
                 ctx: zmq.Context = None,
                 get_envelope_kwargs: dict = None):
        """Initializes the controller.

        Args:
            ctrl_url: our control server address, in zmq format.
            pub_url: our publishing address, in zmq format.
            loop_sleep_ms: how long we sleep in our main loop, in ms.
            get_envelope_given_proto: method that maps from proto message to
                our desired publisher 'envelope' string.
            ctx: zmq Context; if not provided, we will create a new instance.
            get_envelope_kwargs: any additional arguments to be fed to
                get_envelope_given_proto.
        """
        if not ctx:
            ctx = zmq.Context.instance()

        self.publisher = pub.Publisher(pub_url, get_envelope_given_proto,
                                       ctx, get_envelope_kwargs)
        self.control_server = control_server.ControlServer(ctrl_url)
        self.loop_sleep_s = loop_sleep_ms / 1000

        # Init our current understanding of state / params
        self.scan_state = self.poll_scan_state()
        self.scan_params = self.poll_scan_params()
        self.scan = self.get_latest_scan()

    @abstractmethod
    def on_start_scan(self) -> ctrl.ControlResponse:
        """Handle a request to start a scan."""

    @abstractmethod
    def on_stop_scan(self) -> ctrl.ControlResponse:
        """Handle a request to stop a scan."""

    @abstractmethod
    def on_set_scan_params(self, scan_params: scan.ScanParameters2d
                           ) -> ctrl.ControlResponse:
        """Handle a request to change the scan parameters."""

    @abstractmethod
    def poll_scan_state(self) -> scan.ScanState:
        """Poll the controller for the current scan state."""

    @abstractmethod
    def poll_scan_params(self) -> scan.ScanParameters2d:
        """Poll the controller for the current scan parameters."""

    @abstractmethod
    def get_latest_scan(self) -> scan.Scan2d:
        """Obtain latest performed scan.

        Return None if you know a scan was not performed. Otherwise,
        we will compare the last scan to the latest to determine if
        the scan succeeded.
        """

    def _handle_polling_device(self):
        """Polls aspects of device, and publishes changes (including scans)."""
        old_scan_state = self.scan_state
        self.scan_state = self.poll_scan_state()

        if old_scan_state != self.scan_state:
            self.publisher.send_msg(self.scan_state)


        if (old_scan_state == scan.ScanState.SS_SCANNING and
                self.scan_state != scan.ScanState.SS_SCANNING):
            old_scan = self.scan
            self.scan = self.get_latest_scan()

            # If scans are different, assume now and send out!
            # TODO: Consider just comparing timestamps!?!?!
            if old_scan != self.scan:
                self.publisher.send_msg(self.scan)

        old_scan_params = self.scan_params
        self.scan_params = self.poll_scan_params()
        if old_scan_params != self.scan_params:
            self.publisher.send_msg(self.scan_params)

    def _handle_incoming_requests(self):
        """Polls control_server for requests and responds to them."""
        req, proto = self.control_server.poll()

        # Refuse most requests while in the middle of a scan
        if (self.scan_state == scan.ScanState.SCANNING and
                req not in self.ALLOWED_COMMANDS_DURING_SCAN):
            self.control_server.reply(ctrl.ControlResponse.REP_PERFORMING_SCAN)
        elif req:
            handler = self.REQ_HANDLER_MAP[req]

            if proto:
                rep = handler(proto)
            else:
                rep = handler()

            self.control_server.reply(rep)


    def run(self):
        """Main loop, where we monitor for requests and publish results."""
        while True:
            self._handle_incoming_requests()
            self._handle_polling_device()
            time.sleep(self.loop_sleep_s)

    # STATIC VARIABLES
    # dict[ctrl.ControlRequest, tuple(Callable, Message)]
    REQ_HANDLER_MAP = MappingProxyType({
        ctrl.ControlRequest.REQ_START_SCAN: on_start_scan,
        ctrl.ControlRequest.REQ_STOP_SCAN:  on_stop_scan,
        ctrl.ControlRequest.REQ_SET_SCAN_PARAMS: on_set_scan_params
    })

    # Indicates commands we will allow to be sent while a scan is ongoing
    ALLOWED_COMMANDS_DURING_SCAN = [ctrl.ControlRequest.REQ_STOP_SCAN]
