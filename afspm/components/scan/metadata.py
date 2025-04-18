"""Saves scan info into a metadata file, for later filtering."""

import logging
import csv
from typing import Any

from .. import component as afspmc

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
        filepath: path + fname of the csv file to save. Default is
            'scan_metadata.csv'.
        control_state: reference to latest control_state, for context in
            saving.
        delimiter: csv delimiter to use. Defaults to csv module default.
        quotechar: csv quotechar to use. Defaults to csv module default.
        quoting: quoting generation determination. Defaults to csv default.
    """

    CSV_FIELDS = ['timestamp(s)', 'filename', 'channel/type', 'control_mode',
                  'client_id', 'problems_set']

    def __init__(self, filepath: str = './scan_metadata.csv',
                 delimiter: str = None, quotechar: str = None,
                 quoting: Any = None, **kwargs):
        """Initialize the writer."""
        self.filepath = filepath
        self.delimiter = delimiter
        self.quotechar = quotechar
        self.quoting = quoting

        self.control_state = None

        logger.debug("Creating initial csv file, with header.")
        with open(self.filepath, 'w', newline='') as csvfile:
            writer = self._create_dict_writer(csvfile)
            writer.writeheader()

        super().__init__(**kwargs)

    def on_message_received(self, envelope: str, proto: Message):
        """Override, save when scans received."""
        if isinstance(proto, control_pb2.ControlState):
            logger.debug("New control state received, storing.")
            self.control_state = proto
        if (isinstance(proto, scan_pb2.Scan2d) or
                isinstance(proto, spec_pb2.Spec1d)):
            logger.debug("Collection received, saving context.")
            self._save_collection_context(proto)

    def _create_dict_writer(self, csvfile: str) -> csv.DictWriter:
        """Create dict writer from internal variables and provided csv file."""
        kwargs_dict = {'f': csvfile, 'fieldnames': self.CSV_FIELDS}
        if self.delimiter is not None:
            kwargs_dict['delimiter'] = self.delimiter
        if self.quotechar is not None:
            kwargs_dict['quotechar'] = self.quotechar
        if self.quoting is not None:
            kwargs_dict['quoting'] = self.quoting

        return csv.DictWriter(**kwargs_dict)

    def _save_collection_context(self, collection: scan_pb2.Scan2d |
                                 spec_pb2.Spec1d):
        """Save the current scan context."""
        if self.control_state is None:
            logger.error("Cannot save metadata: we have not received context.")

        row_vals = self._get_metadata_row(collection)
        row_dict = dict(zip(self.CSV_FIELDS, row_vals))

        with open(self.filepath, 'a', newline='') as csvfile:
            writer = self._create_dict_writer(csvfile)
            writer.writerow(row_dict)

    def _get_metadata_row(self, collection: scan_pb2.Scan2d |
                          spec_pb2.Spec1d) -> [str]:
        if isinstance(collection, scan_pb2.Scan2d):
            row_vals = self._get_scan_metadata(collection)
        else:
            row_vals = self._get_spec_metadata(collection)
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
