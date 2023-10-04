"""Holds Abstract Device Controller Class (defines controller logic)."""

import logging
import time
import datetime
import copy
from abc import ABCMeta, abstractmethod
from typing import Callable
from types import MappingProxyType
import zmq
from google.protobuf.message import Message

from ..afspm import component as afspmc

from ...io import common
from ...io.pubsub import publisher as pub
from ...io.pubsub import subscriber as sub
from ...io.control import commands as cmd
from ...io.control import server as ctrl_srvr

from ...io.protos.generated import scan_pb2
from ...io.protos.generated import control_pb2


logger = logging.getLogger(__name__)


class DeviceController(afspmc.AfspmComponent, metaclass=ABCMeta):
    """Handles communicating with SPM device and handling requests.

    The DeviceController is the principal node for communicating with an SPM
    device (usually via an actual SPM controller). It is responsible for:
    - Receiving requests from a ControlClient and responding to them;
    - Sending appropriate requests to the device itself, to perform actions;
    - Monitoring the SPM device for state changes, and reporting these changes
    to any listeners via its publisher;
    - Sending out any performed scans out to listeners via its Publisher.

    It communicates with any ControlClients via a zmq REP node, where it
    receives requests and handles them via its appropriate methods (e.g.
    on_start_scan()).

    It sends out state changes and scans via a zmq PUB node, where it publishes
    these aspects.

    This is an abstract class, as device communication is SPM controller
    specific. We expect a DeviceController child class for a given SPM
    controller.

    Note: we allow providing a subscriber to DeviceController (it inherits
    from AspmComponent). If subscribed to the PubSubCache, it will receive
    kill signals and shutdown appropriately.

    Attributes:
        publisher: Publisher instance, for publishing data.
        control_server: ControlServer instance, for responding to control
            requests.
        req_handler_map: mapping from ControlRequest to method to call, for
            ease of use within some of the methods.
        scan_state: device's current ScanState.
        scan_params; device's current ScanParameters2d.
        scan: device's most recent Scan2d.
        subscriber: optional subscriber, to hook into (and detect) kill
            signals.
    """
    TIMESTAMP_ATTRIB_NAME = 'timestamp'

    # Indicates commands we will allow to be sent while not free
    ALLOWED_COMMANDS_WHILE_NOT_FREE = [control_pb2.ControlRequest.REQ_STOP_SCAN]

    # Note: REQ_HANDLER_MAP defined at end, due to dependency on methods
    # defined below.

    def __init__(self, name: str, publisher: pub.Publisher,
                 control_server: ctrl_srvr.ControlServer,
                 loop_sleep_s: int = common.LOOP_SLEEP_S,
                 hb_period_s: float = common.HEARTBEAT_PERIOD_S,
                 ctx: zmq.Context = None, subscriber: sub.Subscriber = None,
                 **kwargs):
        """Initializes the controller.

        Args:
            name: component name.
            publisher: Publisher instance, for publishing data.
            control_server: ControlServer instance, for responding to control
                requests.
            loop_sleep_s: how long we sleep in our main loop, in s.
            hb_period_s: how frequently we should send a hearbeat.
            ctx: zmq Context; if not provided, we will create a new instance.
            subscriber: optional subscriber, to hook into (and detect) kill
                signals.
            kwargs: allows non-used input arguments to be passed (so we can
                initialize from an unfiltered dict).
        """
        if not ctx:
            ctx = zmq.Context.instance()

        self.publisher = publisher
        self.control_server = control_server
        self.req_handler_map = self.create_req_handler_map()

        # Init our current understanding of state / params
        self.scan_state = scan_pb2.ScanState.SS_UNDEFINED
        self.scan_params = scan_pb2.ScanParameters2d()
        self.scans = []

        # AfspmComponent constructor: no control_client provided, as that
        # logic is handled by the control_server.
        super().__init__(name, subscriber=subscriber, control_client=None,
                         ctx=ctx, loop_sleep_s=loop_sleep_s,
                         hb_period_s=hb_period_s)

    def create_req_handler_map(self) -> dict[control_pb2.ControlRequest, Callable]:
        """Create our req_handler_map, for mapping REQ to methods."""
        return MappingProxyType({
            control_pb2.ControlRequest.REQ_START_SCAN: self.on_start_scan,
            control_pb2.ControlRequest.REQ_STOP_SCAN:  self.on_stop_scan,
            control_pb2.ControlRequest.REQ_SET_SCAN_PARAMS: self.on_set_scan_params})


    @abstractmethod
    def on_start_scan(self) -> control_pb2.ControlResponse:
        """Handle a request to start a scan."""

    @abstractmethod
    def on_stop_scan(self) -> control_pb2.ControlResponse:
        """Handle a request to stop a scan."""

    @abstractmethod
    def on_set_scan_params(self, scan_params: scan_pb2.ScanParameters2d
                           ) -> control_pb2.ControlResponse:
        """Handle a request to change the scan parameters."""

    @abstractmethod
    def poll_scan_state(self) -> scan_pb2.ScanState:
        """Poll the controller for the current scan state."""

    @abstractmethod
    def poll_scan_params(self) -> scan_pb2.ScanParameters2d:
        """Poll the controller for the current scan parameters."""

    @abstractmethod
    def poll_scans(self) -> list[scan_pb2.Scan2d]:
        """Obtain latest performed scans.

        We will compare the prior scans (or first of each) to the latest to
        determine if the scan succeeded (i.e. they are different).

        Note that we will first consider the timestamp attribute when
        comparing scans. If this attribute is not passed, we will do
        a data comparison.

        To read the creation time of a file using Python, use
            get_file_creation_datetime()
        and you can put that in the timestamp param with:
            scan.timestamp.FromDatetime(ts)
        """

    def _handle_polling_device(self):
        """Polls aspects of device, and publishes changes (including scans).

        Note: we expect scan state to be sent *last*, so that any client has
        the ability to validate the expected changes have taken effect. Put
        differently: any client should get all other changes *before* the
        state change.
        """
        old_scan_state = copy.deepcopy(self.scan_state)
        self.scan_state = self.poll_scan_state()

        if (old_scan_state == scan_pb2.ScanState.SS_SCANNING and
                self.scan_state != scan_pb2.ScanState.SS_SCANNING):
            old_scans = copy.deepcopy(self.scans)
            self.scans = self.poll_scans()

            # If scans are different, assume now and send out!
            # Test timestamps if they exist. Otherwise, compare
            # data arrays.
            send_scan = False

            both_have_scans = len(self.scans) > 0 and len(old_scans) > 0
            only_new_has_scans = len(self.scans) > 0 and len(old_scans) == 0
            timestamps_different = (
                both_have_scans and
                self.scans[0].HasField(self.TIMESTAMP_ATTRIB_NAME) and
                old_scans[0].HasField(self.TIMESTAMP_ATTRIB_NAME) and
                self.scans[0].timestamp != old_scans[0].timestamp)
            values_different = (both_have_scans and
                                self.scans[0].values != old_scans[0].values)

            if (only_new_has_scans or (timestamps_different or
                                       values_different)):
                send_scan = True

            if send_scan:
                logger.info("New scans, sending out.")
                for scan in self.scans:
                    self.publisher.send_msg(scan)

        old_scan_params = copy.deepcopy(self.scan_params)
        self.scan_params = self.poll_scan_params()
        if old_scan_params != self.scan_params:
            logger.info("New scan_params, sending out.")
            self.publisher.send_msg(self.scan_params)

        # Scan state changes sent *last*!
        if old_scan_state != self.scan_state:
            logger.info("New scan state %s, sending out.",
                        common.get_enum_str(scan_pb2.ScanState,
                                            self.scan_state))
            scan_state_msg = scan_pb2.ScanStateMsg(scan_state=self.scan_state)
            self.publisher.send_msg(scan_state_msg)

    def _handle_incoming_requests(self):
        """Polls control_server for requests and responds to them."""
        req, proto = self.control_server.poll()
        if req:  # Ensure we received something
            # Refuse most requests while moving/scanning (not free)
            if (self.scan_state != scan_pb2.ScanState.SS_FREE and
                    req not in self.ALLOWED_COMMANDS_WHILE_NOT_FREE):
                self.control_server.reply(
                    control_pb2.ControlResponse.REP_NOT_FREE)
            else:
                handler = self.req_handler_map[req]
                rep = handler(proto) if proto else handler()

                # Special case! If scan was cancelled and succeeded, we
                # send out an SS_INTERRUPTED state, to allow detecting
                # interruptions.
                if (req == control_pb2.ControlRequest.REQ_STOP_SCAN and
                        rep == control_pb2.ControlResponse.REP_SUCCESS):
                    scan_state_msg = scan_pb2.ScanStateMsg(
                        scan_state=scan_pb2.ScanState.SS_INTERRUPTED)
                    logger.info("Scan interrupted, sending out %s.",
                                common.get_enum_str(scan_pb2.ScanState,
                                                    scan_state_msg.scan_state))
                    self.publisher.send_msg(scan_state_msg)

                self.control_server.reply(rep)


    def run_per_loop(self):
        """Where we monitor for requests and publish results."""
        self._handle_incoming_requests()
        self._handle_polling_device()


def get_file_creation_datetime(filename: str) -> datetime.datetime:
    """Read creation time of a file, return a datetime representing it.

    Taken from: https://stackoverflow.com/questions/237079/how-do-i-get-file-
    creation-and-modification-date-times.
    """
    return datetime.datetime.fromtimestamp(filename.stat().st_ctime,
                                           tz=datetime.timezone.utc)
