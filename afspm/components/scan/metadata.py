"""Saves scan info into a metadata file, for later filtering."""

import logging

from .. import component as afspmc
from ...utils import csv
from typing import Any

from google.protobuf.message import Message

from ...io.protos.generated import scan_pb2
from ...io.protos.generated import control_pb2
from ...io.protos.generated import spec_pb2


logger = logging.getLogger(__name__)


class ScanMetadataWriter(afspmc.AfspmComponentBase):
    """Saves scan info into a metadata file, for later filtering.

    The ScanMetadataWriter concerns itself with storing the 'context' around
    all scans performed during an experiment. In doing so, it allows filtering
    of salient data (e.g., the desired scans performed by your experiment) from
    those related to 'fixing' the SPM during the experiment (e.g., any scans
    done to fix the PID feedback loop, if it was determined to be faulty).

    This data is stored in a single comma-separated value (CSV) file per
    experiment. Any scripting service could then be used to move/filter the
    data of interest accordingly.

    Note that we explicitly *do not* interfere with the scan saving procedure
    performed by the SPM controller. While it may seem ideal to filter files
    *during* the experiment: (a) this imposes an additional requirement on
    the SPM and microscope translator, and (b) our desired 'filter' may change
    between the running of an experiment and after-the-fact analysis. Because
    of both of these reasons, we resort to simply logging context.

    Currently, we store the following:
    # timestamp(s), filename, channel, control_mode, client_id, problems_set
    (for timestamp clarification, see
    https://protobuf.dev/reference/protobuf/google.protobuf/#timestamp)

    Note that problems_set is an array. We convert it into a str when writing.
    It can be converted back into an array via ast.literal_eval().

    Attributes:
        csv_attribs: attributes associated with the csv file we will be saving
            to.
        control_state: reference to latest control_state, for context in
            saving.
    """

    CSV_FIELDS = ['timestamp(s)', 'filename', 'channel/type', 'control_mode',
                  'client_id', 'problems_set']

    DEFAULT_CSV_ATTRIBS = csv.CSVAttributes('scan_metadata.csv')

    def __init__(self, csv_attribs: csv.CSVAttributes, **kwargs):
        """Initialize the writer."""
        self.csv_attribs = csv_attribs
        self.control_state = None

        csv.init_csv_file(self.csv_attribs, self.CSV_FIELDS)
        super().__init__(**kwargs)

    def on_message_received(self, envelope: str, proto: Message):
        """Override, save when scans received."""
        if isinstance(proto, control_pb2.ControlState):
            logger.debug("New control state received, storing.")
            self.control_state = proto
        if (isinstance(proto, scan_pb2.Scan2d) or
                isinstance(proto, spec_pb2.Spec1d)):
            logger.debug("Collection received, saving context.")

            row_vals = self._get_metadata_row(proto)
            csv.save_csv_row(self.csv_attribs, self.CSV_FIELDS,
                             row_vals)

    def _get_metadata_row(self, collection: scan_pb2.Scan2d |
                          spec_pb2.Spec1d) -> [str]:
        if isinstance(collection, scan_pb2.Scan2d):
            row_vals = self._get_scan_metadata(collection)
        else:
            row_vals = self._get_spec_metadata(collection)

        if self.control_state is None:
            logger.warning("We have not received ControlMode. Setting "
                           "associated values to None.")
            row_vals.extend([None, None, None])
        else:
            row_vals.extend([self.control_state.control_mode,
                            self.control_state.client_in_control_id,
                            str(self.control_state.problems_set)])
        return row_vals

    @staticmethod
    def _get_scan_metadata(scan: scan_pb2.Scan2d) -> list[Any]:
        return [scan.timestamp.seconds, scan.filename, scan.channel]

    @staticmethod
    def _get_spec_metadata(spec: spec_pb2.Spec1d) -> list[Any]:
        return [spec.timestamp.seconds, spec.filename, spec.type]
