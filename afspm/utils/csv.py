"""CSV writing helpers."""

import logging
from dataclasses import dataclass
import csv
from typing import Any, TextIO

from ..io.protos.generated import control_pb2


logger = logging.getLogger(__name__)


@dataclass
class CSVAttributes:
    """Attributes for a CSV file."""

    filepath: str
    delimiter: str = None
    quotechar: str = None
    quoting: Any = None


def create_dict_writer(csv_file: TextIO,
                       csv_attribs: CSVAttributes,
                       csv_fields: list[str]) -> csv.DictWriter:
    """Create dict writer from for path given attributes and fields.

    Args:
        csv_file: TextIO of opened file descriptor, for writing.
        csv_attribs: structure of attributes associated with a csv file
            (such as filepath).
        csv_fields: list of field names.

    Returns:
        DictWriter associated to this CSV file and config.
    """
    kwargs_dict = {'f': csv_file, 'fieldnames': csv_fields}
    if csv_attribs.delimiter is not None:
        kwargs_dict['delimiter'] = csv_attribs.delimiter
    if csv_attribs.quotechar is not None:
        kwargs_dict['quotechar'] = csv_attribs.quotechar
    if csv_attribs.quoting is not None:
        kwargs_dict['quoting'] = csv_attribs.quoting

    return csv.DictWriter(**kwargs_dict)


def init_csv_file(csv_attribs: CSVAttributes,
                  csv_fields: list[str]):
    """Initialize a csv file with header fields.

    Opens a CSV file and writes the header fields to it.

    Args:
        csv_attribs: structure of attributes associated with a csv file
            (such as filepath).
        csv_fields: list of field names.
    """
    logger.debug("Creating initial csv file, with header.")
    with open(csv_attribs.filepath, 'w', newline='') as csv_file:
        writer = create_dict_writer(csv_file, csv_attribs, csv_fields)
        writer.writeheader()


def save_csv_row(csv_attribs: CSVAttributes,
                 csv_fields: list[str],
                 control_state: control_pb2.ControlState,
                 row_vals: list[Any]):
    """Save a row of 'context' to a CSV file.

    Opens the CSV file and writes a row of data, i.e. the 'context' of the
    row vals.

    Args:
        csv_attribs: structure of attributes associated with a csv file
            (such as filepath).
        csv_fields: list of field names.
        control_state: the current ControlState of the system, which determines
            whether or not we have received context this pass.
        row_vals: list of values associated to a row.
    """
    if control_state is None:
        logger.error("Cannot save metadata: we have not received context.")

    row_dict = dict(zip(csv_fields, row_vals))

    with open(csv_attribs.filepath, 'a', newline='') as csv_file:
        writer = create_dict_writer(csv_file, csv_attribs, csv_fields)
        writer.writerow(row_dict)
