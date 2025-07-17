"""Test metadata writing logic."""

import logging
import pytest
import tempfile
import datetime as dt

from afspm.components.scan import metadata
from afspm.io import common
from afspm.utils import csv

from afspm.io.protos.generated import control_pb2
from afspm.io.protos.generated import scan_pb2
from afspm.io.protos.generated import spec_pb2

logger = logging.getLogger(__name__)


class FakeMetadataWriter(metadata.ScanMetadataWriter):
    def __init__(self, csv_attribs: csv.CSVAttributes,
                 control_state: control_pb2.ControlState, **kwargs):
        """Initialize the writer. Same as parent, but no csv stuff."""
        self.csv_attribs = csv_attribs
        super().__init__(csv_attribs, **kwargs)
        self.official_control_state = control_state


# ----- Fixtures ----- #
@pytest.fixture
def scan_2d():
    scan_params = common.create_scan_params_2d([0, 0], [200, 300],
                                               'nm')
    return scan_pb2.Scan2d(params=scan_params, channel='banana',
                           filename='oh bai')


@pytest.fixture
def spec_1d():
    return spec_pb2.Spec1d(type='hammock', filename='oh hai')


@pytest.fixture
def control_state():
    return control_pb2.ControlState(
        control_mode=control_pb2.ControlMode.CM_AUTOMATED,
        client_in_control_id='id0',
        problems_set=[control_pb2.ExperimentProblem.EP_NONE])


@pytest.fixture
def fake_metadata_writer(control_state):
    csv_attribs = csv.CSVAttributes(filepath=tempfile.gettempdir() +
                                    '/fake_metadata_path.csv')
    return FakeMetadataWriter(csv_attribs=csv_attribs,
                              control_state=control_state, name='fakewriter')


# ----- Tests ----- #
def test_writing(fake_metadata_writer, scan_2d, spec_1d, control_state):
    logger.info('Validate that we can write scan params and probe position.')

    rows = fake_metadata_writer._get_metadata_row(scan_2d)
    exp_rows = [scan_2d.timestamp.ToDatetime(dt.timezone.utc).isoformat(),
                'oh bai', 'banana',
                common.get_enum_str(control_pb2.ControlMode,
                                    control_pb2.ControlMode.CM_AUTOMATED),
                'id0',
                common.get_enum_str(control_pb2.ExperimentProblem,
                                    control_pb2.ExperimentProblem.EP_NONE)]
    assert rows == exp_rows

    rows = fake_metadata_writer._get_metadata_row(spec_1d)
    exp_rows = [spec_1d.timestamp.ToDatetime(dt.timezone.utc).isoformat(),
                'oh hai', 'hammock',
                common.get_enum_str(control_pb2.ControlMode,
                                    control_pb2.ControlMode.CM_AUTOMATED),
                'id0',
                common.get_enum_str(control_pb2.ExperimentProblem,
                                    control_pb2.ExperimentProblem.EP_NONE)]
    assert rows == exp_rows

    control_state.problems_set[:] =[
        control_pb2.ExperimentProblem.EP_TIP_SHAPE_CHANGED]
    fake_metadata_writer.control_state = control_state

    exp_rows = [spec_1d.timestamp.ToDatetime(dt.timezone.utc).isoformat(),
                'oh hai', 'hammock',
                common.get_enum_str(control_pb2.ControlMode,
                                    control_pb2.ControlMode.CM_AUTOMATED),
                'id0',
                common.get_enum_str(
                    control_pb2.ExperimentProblem,
                    control_pb2.ExperimentProblem.EP_TIP_SHAPE_CHANGED)]
    rows = fake_metadata_writer._get_metadata_row(spec_1d)
    assert rows == exp_rows
