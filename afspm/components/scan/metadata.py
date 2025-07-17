"""Saves scan info into a metadata file, for later filtering."""

import logging
import datetime as dt

from .. import component as afspmc
from ...utils import csv
from typing import Any

from google.protobuf.message import Message

from ...io import common
from ...io.protos.generated import scan_pb2
from ...io.protos.generated import control_pb2
from ...io.protos.generated import spec_pb2


logger = logging.getLogger(__name__)


CLIENT_IN_CONTROL_ID = 'client_in_control_id'


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
    # datetime, filename, channel, control_mode, client_id, problems_set
    Note that the datetime is in UTC.

    Note that problems_set is an array. We convert it into a str when writing.
    It can be converted back into an array via ast.literal_eval().

    Attributes:
        csv_attribs: attributes associated with the csv file we will be saving
            to.
        control_state: reference to latest control_state, for context in
            saving.
    """

    CSV_FIELDS = ['datetime', 'filename', 'channel/type', 'control_mode',
                  'last_client_id', 'problems_set']

    DEFAULT_CSV_ATTRIBS = csv.CSVAttributes('scan_metadata.csv')
    SCANNING_STATES = [scan_pb2.ScopeState.SS_SCANNING,
                       scan_pb2.ScopeState.SS_SPEC]

    def __init__(self, csv_attribs: csv.CSVAttributes = DEFAULT_CSV_ATTRIBS,
                 **kwargs):
        """Initialize the writer."""
        self.csv_attribs = csv_attribs
        self.official_control_state = None
        self.latest_control_state = None
        self.scope_state = None

        csv.init_csv_file(self.csv_attribs, self.CSV_FIELDS)
        super().__init__(**kwargs)

    def on_message_received(self, envelope: str, proto: Message):
        """Override, save when scans received."""
        if (isinstance(proto, control_pb2.ControlState)):
            self.latest_control_state = proto
        if (isinstance(proto, scan_pb2.Scan2d) or
                isinstance(proto, spec_pb2.Spec1d)):
            row_vals = self._get_metadata_row(proto)
            csv.save_csv_row(self.csv_attribs, self.CSV_FIELDS,
                             row_vals)
        if isinstance(proto, scan_pb2.ScopeStateMsg):
            if self.scope_state:  # update control state when scanning
                was_not_scan = self.scope_state not in self.SCANNING_STATES
                is_scan = proto.scope_state in self.SCANNING_STATES
                if was_not_scan and is_scan:
                    self.official_control_state = self.latest_control_state
            else:
                self.official_control_state = self.latest_control_state
            self.scope_state = proto.scope_state

    def _get_metadata_row(self, collection: scan_pb2.Scan2d |
                          spec_pb2.Spec1d) -> [str]:
        if isinstance(collection, scan_pb2.Scan2d):
            row_vals = self._get_scan_metadata(collection)
        else:
            row_vals = self._get_spec_metadata(collection)

        if self.official_control_state is None:
            logger.warning("We have not received ControlMode. Setting "
                           "associated values to None.")
            row_vals.extend([None, None, None])
        else:
            control_mode_str = common.get_enum_str(
                control_pb2.ControlMode,
                self.official_control_state.control_mode)
            problems_set_strs = [
                common.get_enum_str(
                    control_pb2.ExperimentProblem, problem)
                for problem in self.official_control_state.problems_set]
            problems_set_str = '-'.join(problems_set_strs)

            row_vals.extend([control_mode_str,
                            self.official_control_state.client_in_control_id,
                            problems_set_str])
        return row_vals

    @staticmethod
    def _get_scan_metadata(scan: scan_pb2.Scan2d) -> list[Any]:
        return [scan.timestamp.ToDatetime(dt.timezone.utc).isoformat(),
                scan.filename, scan.channel]

    @staticmethod
    def _get_spec_metadata(spec: spec_pb2.Spec1d) -> list[Any]:
        return [spec.timestamp.ToDatetime(dt.timezone.utc).isoformat(),
                spec.filename, spec.type]
