"""Visualizer that resets all scans in cache at an interval."""

import logging
from typing import Callable
import numpy as np

from google.protobuf.message import Message

from afspm.spawn import LOGGER_ROOT
from afspm.components.visualizer import Visualizer
from afspm.io.protos.generated import scan_pb2


logger = logging.getLogger(LOGGER_ROOT + '.samples.image_roi.' + __name__)


class ResetScansVisualizer(Visualizer):
    """Visualizer that resets all scans in cache at an interval.

    Every N scans, this visualizer will delete all prior scans in its cache,
    using the provided scan_id.

    Attributes:
        num_scans_before_reset: how many scans to receive before resetting
            scans in our cache.
        scans_since_reset: current reference of how many scans we have received
            since a reset.
    """

    def __init__(self, num_scans_before_reset: int, **kwargs):
        self.num_scans_before_reset = num_scans_before_reset
        self.scans_since_reset = 0

        super().__init__(**kwargs)

    def on_message_received(self, envelope: str, proto: Message):
        """Override: we update our scan counter and reset if needed."""
        if isinstance(proto, scan_pb2.Scan2d):
            self.scans_since_reset += 1

            if self.scans_since_reset > self.num_scans_before_reset:
                self.scans_since_reset = 0
                # Reset all cache keys that are scan related.
                for key in list(self.subscriber.cache.keys()):
                    if self.scan_id in key:
                        del self.subscriber.cache[key]
        super().on_message_received(envelope, proto)
