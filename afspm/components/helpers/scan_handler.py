"""Class to simplify setting scan params and getting a scan."""

import logging
import time
import copy
from typing import Callable, Any

from google.protobuf.message import Message

from ...io import common

from ...io.protos.generated import geometry_pb2
from ...io.protos.generated import scan_pb2
from ...io.protos.generated import control_pb2

from ...io.control.control_client import ControlClient


logger = logging.getLogger(__name__)


# TODO: Write unit test for this.
class ScanHandler:
    """Simplifies requesting a scan from a DeviceController.

    The ScanHandler encapsulates the procedure of getting a scan from a
    DeviceController, which involes:
    - Ensuring we have control of the DeviceController.
    - Setting the scan parameters, which may involve moving the device.
    - Obtaining the scan, once the device has been setup (and moved to the
    right location).

    If we do not have control, it will log this and continue requesting control
    between sleeps of rerun_wait_s.

    If the DeviceController returns an unexpected error, it will log this and
    retry the full request (ensuring parameters are set) between sleeps of
    rerun_wait_s.

    To use within an AfspmComponent:
    - Call on_message_received() within the component's on_message_received().
    - Call handle_resends() within the component's run_per_loop().

    Attributes:
        rerun_wait_s: how long to wait before rerunning our scan (on issues
            with the DeviceController).
        get_next_params: method to determine the next scan_params to scan. If
            None is provided, it will log this and retry later.
        next_params_kwargs: kwargs for get_next_params.
        control_mode_to_run: ControlMode to run under. If the controller is not
            in this control mode, we do nothing until they switch to it.

        control_mode: current control mode the controller is in.
        scan_params: current ScanParameters2d instance.
        scan_state: current ScanState.
        desired_scan_state: desired ScanState.
        rerun_scanning_logic: whether or not we need to potentially rerun a
            scan.
        rerun_sleep_ts: a timestamp for determining when to rerun a scan.
    """

    def __init__(self, rerun_wait_s: int,
                 get_next_params: Callable[[Any], scan_pb2.ScanParameters2d],
                 next_params_kwargs: dict = {},
                 control_mode: control_pb2.ControlMode =
                 control_pb2.ControlMode.CM_AUTOMATED):
        self.rerun_wait_s = rerun_wait_s
        self.get_next_params = get_next_params
        self.next_params_kwargs = next_params_kwargs
        self.control_mode_to_run = control_mode

        self.control_mode = control_pb2.ControlMode.CM_UNDEFINED
        self.scan_params = scan_pb2.ScanParameters2d()
        self.scan_state = scan_pb2.ScanState.SS_UNDEFINED
        self.desired_scan_state = scan_pb2.ScanState.SS_UNDEFINED

        self.rerun_scanning_logic = False
        self.rerun_sleep_ts = None

    def on_message_received(self, proto: Message,
                            control_client: ControlClient):
        """Handle scanning logic on an AfspmComponent's message receipt.

        This will update the current scan_state and desired_scan_state, and
        send out the next request toward performing a scan.

        It should be called within the associated AfspmComponent's
        on_message_received() method.

        Args:
            proto: Protobuf message received by the AfspmComponent.
            control_client: AfspmComponent's control_client.
        """
        if isinstance(proto, control_pb2.ControlState):
            self.control_mode = proto.control_mode
        if isinstance(proto, scan_pb2.ScanStateMsg):
            self._handle_scan_state_receipt(proto)
            self._perform_scanning_logic(control_client)

    def handle_resends(self, control_client: ControlClient):
        """Handle resending requests on DeviceController delays/issues.

        This will handle resending requests if we receive delays/issues from
        the DeviceController. It should be called in the associated
        AfspmComponent's run_per_loop() method.

        Note: we assume any appropriate per-loop delaying is handled by the
        AfspmComponent using this handler.

        Args:
            control_client: AfspmComponent's control_client.
        """
        in_desired_control_mode = self.control_mode_to_run == self.control_mode
        if in_desired_control_mode and self.rerun_scanning_logic:
            need_to_wait = self.rerun_sleep_ts is not None
            enough_time_has_passed = (need_to_wait and
                                      (time.time() - self.rerun_sleep_ts >
                                       self.rerun_wait_s))
            if not need_to_wait or enough_time_has_passed:
                self.rerun_sleep_ts = None
                self.rerun_scanning_logic = False
                self._perform_scanning_logic(control_client)

    def _handle_scan_state_receipt(self, proto: scan_pb2.ScanStateMsg):
        """Updates the desired scan state (getting next scan params if needed).

        If a scan is finished, it also requests the next scan parameters via
        get_next_params().

        Args:
            proto: received ScanStateMsg protobuf from the AfspmComponent.
        """
        logger.debug("Received new scan state: %s",
                     common.get_enum_str(scan_pb2.ScanState,
                                         proto.scan_state))
        last_state = copy.deepcopy(self.scan_state)
        self.scan_state = proto.scan_state

        # Handling desired state logic
        first_startup = (last_state == scan_pb2.ScanState.SS_UNDEFINED and
                         self.scan_state == scan_pb2.ScanState.SS_FREE)
        interrupted = self.scan_state == scan_pb2.SS_INTERRUPTED
        finished_scanning = (last_state == scan_pb2.ScanState.SS_SCANNING and
                             self.scan_state == scan_pb2.ScanState.SS_FREE)
        finished_moving = (last_state == scan_pb2.ScanState.SS_MOVING and
                           self.scan_state == scan_pb2.ScanState.SS_FREE)

        if interrupted:
            logger.info("A scan was interrupted! Will restart what we were "
                        "doing.")
            self.desired_scan_state = scan_pb2.ScanState.SS_MOVING
        elif first_startup or finished_scanning:
            if first_startup:
                logger.info("First startup, sending first scan params.")
            else:
                logger.info("Finished scan, preparing next scan params.")
            self.scan_params = self.get_next_params(**self.next_params_kwargs)
            self.desired_scan_state = scan_pb2.ScanState.SS_MOVING
        elif finished_moving:
            logger.info("Finished moving, will request scan.")
            self.desired_scan_state = scan_pb2.ScanState.SS_SCANNING

    def _perform_scanning_logic(self, control_client: ControlClient):
        """Requests the next scan aspect from client.

        Requests the appropriate scan aspect (e.g. set_scan_params, start_scan)
        for the current scan. Handles reruns if a request fails.

        TODO: We are definitely missing the feedback control.

        Args:
            control_client: AfspmComponent's ControlClient.
        """
        in_desired_control_mode = self.control_mode_to_run == self.control_mode
        scan_state_undefined = (scan_pb2.ScanState.SS_UNDEFINED in
                                (self.scan_state, self.desired_scan_state))
        if scan_state_undefined or not in_desired_control_mode:
            logger.debug("Not performing scanning logic because ScanState "
                         "undefined or ControlMode not desired one.")
            self.rerun_scanning_logic = True
            return  # Early return, we're not ready yet.

        # Handle sending requests (not guaranteed it will work!)
        if self.scan_state != self.desired_scan_state:
            logger.info("In state %s, wanting state %s; requesting.",
                        common.get_enum_str(scan_pb2.ScanState,
                                            self.scan_state),
                        common.get_enum_str(scan_pb2.ScanState,
                                            self.desired_scan_state))
            if self.desired_scan_state == scan_pb2.ScanState.SS_MOVING:
                if not self.scan_params:
                    logger.info("Cannot send scan params, because "
                                "get_next_params returned None."
                                "Sleeping and retrying.")
                    self.rerun_scanning_logic = True
                    return
                rep = control_client.set_scan_params(self.scan_params)
            elif self.desired_scan_state == scan_pb2.ScanState.SS_SCANNING:
                rep = control_client.start_scan()

            if rep == control_pb2.ControlResponse.REP_SUCCESS:
                logger.info("Request succeeded.")
                return

            logger.info("Request failed with rep %s!",
                        common.get_enum_str(control_pb2.ControlResponse,
                                            rep))
            self.rerun_scanning_logic = True

            if rep == control_pb2.ControlResponse.REP_NOT_IN_CONTROL:
                # We failed due to a control issue. Try to resolve.
                logger.info("Requesting control...")
                rep = control_client.request_control(
                    control_pb2.ControlMode.CM_AUTOMATED)
                if rep == control_pb2.ControlResponse.REP_SUCCESS:
                    logger.info("Control received. Retrying...")
                    self.rerun_sleep_ts = None
                    return

            logger.info("Sleeping and retrying later.")
            self.rerun_sleep_ts = time.time()
