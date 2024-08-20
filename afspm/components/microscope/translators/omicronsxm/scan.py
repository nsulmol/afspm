"""Holds the logic to retrieve and convert scans.

For omicron, the formats are:
.int: raw data. The more useful format according to people who use the
      instrument, so it is the default file extension for get_latest_scan_name.
.bmp: less used format, used for quick visualisation of data 
TODO update comments
"""

from glob import glob
import os
import logging


logger = logging.getLogger(__name__)


def get_latest_scan_metadata_path(dir: str) -> str:
    """Return the location of the metadata for the latest scan.

    Returns the name of the metadata (.txt) file for the latest scan in the
    given folder. It can then be fed to pifm.PiFMTranslator to load the
    data into a sidpy dataset.

    Args:
        dir: path of directory containing all scan sub-directories

    Returns:
        path to most recent metadata (txt) file.

    Raises:
        FileNotFoundError if there are no scans   #TODO maybe return empty scan instead
        ValueError if the file structure is incorrect (i.e. there are
            no/several metadata files in a sub-directory).
    """
    try:
        latest_dir = max(glob(os.path.join(dir, '*/')), key=os.path.getmtime)
    except ValueError:
        #TODO should this be warning or error? could ignore and return nothing/empty scan
        msg = ("No sub-directory (scans) found in cave directory when "
               "polling latest scan")
        logger.error(msg)
        raise FileNotFoundError(msg)

    try:
        txts = glob(os.path.join(latest_dir, "*.txt"))
        if len(txts) != 1:      #there should only be one .txt per dir.
            # TODO: check that this is actually the case
            msg = (f"Found {len(txts)} txt files in scan directory " +
                   f"{latest_dir} when there should only be one.")
            logger.error(msg)
            raise ValueError(msg)
    except ValueError:
        msg = ("No metadata text file found in the latest dir in the scan "
               "directory")
        logger.warning(msg)
        raise ValueError(msg)

    return txts[0]

    # if all scans were in one dir together:
    # filespath = os.path.join(folder,"*.txt")
    # files = glob(filespath)
    # latest = max(files, key=os.path.getctime)

    # return latest
