"""Holds Omicron controller parameters and logic to set and get them.

NOTE: ScanPara() expects a string but GetScanPara() returns a float.
    TODO period at the end of docstring title?
    TODO put dashes back in notes, etc. 
    TODO remove plus signs in strings
"""

import enum
import logging
from typing import Any
from types import MappingProxyType  # Immutable dict

from afspm.components.microscope import params
from afspm.utils import units

from ...io.protos.generated import scan_pb2
from afspm.io.protos.generated import feedback_pb2

from SXMRemote import DDEClient

logger = logging.getLogger(__name__)


# ----- Omicron Params ----- #
class OmicronParameter(str, enum.Emum):  # TODO why not "OmicronParameterS"?
    """Omicron internal parameters.

    NOTE: Omicron keeps track of the scan region using the coordinates of the
    center of the scanning region instead of the top-left as the rest of AFSPM
    does. This is remedied by calculating the position of the center of the ROI
    from the position of the top left and the size of the region, given by the 
    user.

    Also NOTE: Omicron only takes square scans. Therefore, if the given x- and
    y- dimensions and resolution are not equal, we take the greatest of the two
    and log a warning indicating we did so. 
    """

    # Physical scan parameters
    TL_X = "X"  # NOTE: X-coordinate of the center, not top left
    TL_Y = "Y"  # idem
    SZ_X = "Range"  # x-dimension of scan region.
    SZ_Y = "Range"  # y-dimension of scan region

    # Digital scan parameters
    RES_X = "Pixel"  # x-resolution
    RES_Y = "Pixel"  # y-resolution. Same as for x because scans are square

    # Feedback parameters
    CP = "Kp"   # proportional gain of main feedback loop.
    CI = "Ki"   # integral gain of main feedback loop.

    # Other
    # TODO SCAN_SPEED_UNITS_S?


class OmicronParameterUnit(str, enum.Enum):
    """Units for the Omicron parameters.

    Name: unit name in Omicron terminology.
    Value: expected physical unit.
    """

    # Physical scan parameters
    X = "nm"
    Y = "nm"
    Range = "nm"

    # Digital scan parameters
    Pixel = None

    # Feedback parameters
    Kp = None
    Ki = None

# TODO? class OmicronChannelIds(enum.Enum):


# TODO finish this
PARAM_METHOD_MAP = MappingProxyType({
#    params.MicroscopeParameter.SCAN_SPEED: # insert scan speed method here
})

# ----- Lists of scan and feedback parameters ----- #
# Omicron classifies parameters as "Scan Parameters" or "Feedback Parameters",
# and we use a different function to set/get the two categories. To that end,
# we keep lists here of the two categories. Also, we note the parameter used to
# start/stop a scan here, to access it in controller.on_start/stop_scan.

# TODO make these from OmicronParameter (all but last 2 are scan param),
# last 2 are feedback
SCAN_PARAM = ["X", "Y", "Range", "Pixel"]
FEEDBACK_PARAM = ["Enable", "Ki", "Kp"]
ON_OFF_PARAM = ["Scan"]

# It's also useful to have a list of the afspm parameters
AFSPM_PARAMS = [e.name for e in OmicronParameter]

# The Anfatec controller only supports some specific resolution values.
# It expects to receive the index of the pixel count (1-indexed) in this list
# rather than the actual value.
# Note: the Anfatec can scan at a resolution of 1024 pixels, but the python
# interface does not support setting it to that value. To scan at 1024, 
# the resolution must then have been set previously, manually.

# TODO can't currently scan at 1024:
# we would need to set it manually and never touch again, but we currently
# require ScanParameters2d messages to have values for every param. therefore,
# the translator will try to set to something else.
# To support this, we would need an edge case where if resolution == 1024,
# we don't try to set the resolution (do nothing)
ANFATEC_RESOLUTION = [32, 64, 128, 256, 512]


# ----- Getters / Setters ----- #
def _set_scan_param(client: DDEClient, attr: str, val: Any) -> None:
    """Set the given scan parameter (not feedback parameter) to given value.

    NOTES:
        - Conversions to Omicron units (nm) must have previously been done.
        - SXMRemote does not handle exceptions if fed a bad parameter name /
        value. Therefore, it is important that "attr" is correct, e.g. by
        taking it from the list of parameter names above.

    Args:
        client: The DDE client connected to the Omicron controller.
        attr: name of attribute, in Omicron terminology.
        val: value to set.

    Raises:
        ParameterError if setting fails
    """
    try:
        client.SendWait(f"ScanPara('{attr}',{val});")
    except Exception as e:
        msg = f"Error setting scan parameter {attr} to {val}: {e}"
        logger.error(msg)
        raise params.ParameterError(msg)


def _set_feedback_param(client: DDEClient, attr: str, val: Any) -> None:
    """Set a feedback / ZCtlr parameter in the same way as set_scan_param().

    This function is separate because a different command is
    required by the Omicron controller to set feedback parameters.
    TODO change like above
    NOTE: SXMRemote does not handle exceptions if fed a bad parameter name / 
    value.

    Args:
        client: The DDE client connected to the Omicron controller. 
        attr: name of attribute, in Omicron terminology.
        val: value to set.

    Raises:
        ParameterError if setting fails
    """
    try:
        client.SendWait(f"FeedPara('{attr}',{val});")
    except Exception as e:
        msg = f"Error setting ZCtrl parameter {attr} to {val}: {e}"
        logger.error(msg)
        raise params.ParameterError(msg)


def set_param(client: DDEClient, attr: str, val: Any) -> None:
    """Set the specified parameter to the provided value.

    Args:
        client: The DDE client connected to the Omicron controller. 
        attr: name of attribute, in Omicron terminology.
        val: value to set, in Omicron units

    Raises:
        ParameterError if setting fails
        ValueError if attr is not recognized (i.e. it isn't in one of the
        following lists: SCAN_PARAM, ON_OFF_PARAM, FEEDBACK_PARAM)
    """
    # because there is no way of telling whether the command to SXMRemote is
    # successful, we ensure here that the parameter name exists
    if attr in SCAN_PARAM or attr in ON_OFF_PARAM: 
        return _set_scan_param(client, attr, val)
    elif attr in FEEDBACK_PARAM:
        return _set_feedback_param(client, attr, val)
    else:
        msg = f"Invalid parameter: {attr}."
        logger.error(msg)
        raise ValueError(msg)


def _get_scan_param(client: DDEClient, attr: str) -> float:
    """ Gets the specified scan parameter's value from the Omicron controller.

    TODO: I'm not very consistent in my naming of "Anfatec's Python interface"
    vs "SXMRemote"
    Args:
        client: The DDE client connected to the Omicron controller. 
        attr: name of the attribute as a string. case-sensitive, no spaces.

    Returns:
        parameter value as a float. For boolean parameters: true = 1.0, 
        false = 0.0.

    Raises:
        Exception (TODO create a type?) if:
            An error is encountered while making the request to the Anfatec 
            Python interface ("SXMRemote")
            SXMRemote returns null as the parameter value
    """
    try:
        val = client.GetScanPara(f"\'{attr}\'")  # TODO is it necessary to escape the quotes?
        if val is not None:
            return val
        else:
            # TODO make nice string here 
            msg = (f"Getting {attr} returned None. This happens when the " +
                   "request sent to the Anfatec controller is not " +
                   "recognized. Verify that the parameter requested is " +
                   "spelled correctly and exists.")
            logger.error(msg)
            raise Exception(msg)  # TODO: Generally, do not raise generic exceptions!

    except Exception as e:
        # Or should I simply allow the code to crash if SXMRemote raises an
        # Exception? TODO
        msg = (f"SXMRemote raised exception when getting {attr}" +
               f"(Warning, exception may be unclear): {e}")
        logger.error(msg)
        raise e


def _get_feedback_param(client: DDEClient, attr: str) -> float:
    """Get the provided feedback parameter's value from the Omicron controller.

    Args:
        client: The DDE client connected to the Omicron controller.
        attr: name of the attribute as a string. case-sensitive, no spaces.

    Returns:
        Parameter value as a float.
        For boolean parameters: true = 1.0, false = 0.0

    Raises:
        Exception (TODO create a type?) if:
            An error is encountered while making the request to the Anfatec
            Python interface ("SXMRemote")
            SXMRemote returns null as the parameter value
    """
    try:
        val = client.GetFeedbackPara(f"\'{attr}\'")
        if val is not None:
            return val
        else:
            # TODO make nice string here 
            msg = (f"Getting {attr} returned None. This happens when the " +
                   "request sent to the Anfatec controller is not recognized." +
                   " Verify that the parameter requested is spelled correctly " +
                   "and exists.")
            logger.error(msg)
            raise Exception(msg)

    except Exception as e:
        # Or should I simply allow the code to crash if there's an error in 
        # Anfatec's code? TODO
        msg = f"Error in Anfatec's Python interface while getting {attr}: {e}"
        logger.error(msg)
        raise Exception(msg)


def get_param(client: DDEClient, attr: str) -> float:
    """Get the given feedback parameter's value.

    Args:
        client: The DDE client connected to the Omicron controller.
        attr: name of the attribute as a string. case-sensitive, no spaces.

    Returns:
        parameter value as a float.
        for boolean parameters: true = 1.0, false = 0.0

    Raises:
    Exception (TODO create a type?) if:
        An error is encountered while making the request to the Anfatec
        Python interface ("SXMRemote")
        SXMRemote returns null as the parameter value.
    """
    if attr in SCAN_PARAM or attr in ON_OFF_PARAM:
        return _get_scan_param(client, attr)
    elif attr in FEEDBACK_PARAM: 
        return _get_feedback_param(client, attr)
    else:
        msg = ("Parameter not supported. Verify spelling. To add support for"
               "a parameter, add its name to one of the lists in "
               "component.microscope.translators.omicron.params.py")
        logger.error(msg)
        raise ValueError(msg)


# ----- Set / Get logic ----- #
def set_pb2_scan_params(client: DDEClient, message: scan_pb2.ScanParameters2d
                        ) -> None:
    """Set all scan params to values contained in a ScanParameters2d message.

    Behaviour:
        Converts the values of the message to omicron units and sets the
        parameters to the converted values.
        If the user provides X- and Y- dimensions or resolutions that
        are not equal, the value is set to the greater of the two, as
        Omicron scans a square area, and a warning is logged.

    Args:
        client: the DDE client connected to the Omicron controller
        message: the ScanParameters2d message containing the
        parameter values to be set, and their current units.

    NOTE: The user is required to send values for all the parameters in
    order to calculate the position of the center of the scan region,
    which is required by the Omicron controller.

    Raises:
        ConversionError if the conversion fails (could be due to a bad
        unit/value being given)
        NParameterError if an invalid parameter value is given
        ValueError is an invalid parameter name is given
    """
    # unpack message TODO should there be a common method in utils for this?
    # could make util return 3 lists: attr: str name, values, units
    # TODO what happens here if message is incomplete -> try to access missing
    # value?

    vals = [message.spatial.roi.top_left.x,
            message.spatial.roi.top_left.y,
            message.spatial.roi.size.x,
            message.spatial.roi.size.y,
            message.data.shape.x,
            message.data.shape.y]

    given_units = [message.spatial.units,
                   message.spatial.units,
                   message.spatial.units,
                   message.spatial.units,
                   None, None]

    # Convert values to omicron units    TODO can use units.convert_list
    vals_converted = []
    for index, val in enumerate(vals):
        # TODO here use uuid from the new util method
        # WARNING: this assumes the lists order matches the order in 
        # OmicronParameter.
        # if error look here, could be wrong syntax
        omicron_equivalent = OmicronParameter[AFSPM_PARAMS[index]].name
        converted = units.convert(val, given_units[index], 
                                  OmicronParameterUnit[omicron_equivalent])
        vals_converted.append(converted)

    # TODO: See if you can switch to this rather than vals-converted (hard to read)
    # x, y, w, h, pix_x, pix_y = vals_converted # BUT DIFFERENT NAMES!

    # Get "Range" as the greatest of the X and Y dimensions
    dimensions = [vals_converted[2], vals_converted[3]]
    if dimensions[0] != dimensions[1]:
        logger.warning("X and Y dimensions are not equal. Taking the largest "+
                       "of the two as the side length of the square scan area")
    range = max(dimensions)

    # Get "Pixel" the same way
    resolution = [vals_converted[4], vals_converted[5]]
    if resolution[0] != resolution[1]:
        logger.warning("X- and Y-resolution are not equal. Taking the largest"+
                       "of the two.")
    pixel = max(resolution)
    try:
        pixel = ANFATEC_RESOLUTION.index(pixel) + 1
    except ValueError:
        msg = ("Tried to set the resolution to an unsupported value.\n" +
               f"Supported values: {ANFATEC_RESOLUTION}")
        logger.error(msg)
        raise params.ParameterError(msg)

    # center x and y
    # TODO do something (error, warning?) if x and y obtained like this are
    # outside the supported range?
    if vals_converted[0] and vals_converted[2]:
        x = vals_converted[0] + 0.5 * vals_converted[2]
    if vals_converted[1] and vals_converted[3]:
        y = vals_converted[1] - 0.5 * vals_converted[3]

    # Set values
    for (attr, val) in zip(SCAN_PARAM, [x, y, range, pixel]):
        set_param(client, attr, val)
    # TODO could call instead _set_scan_param(), any difference?


def set_pb2_feedback_params(client: DDEClient,
                            message: feedback_pb2.ZCtrlParameters) -> None:
    """Set all feedback params to values contained in ZCtrlParameters message.

    Args:
        client: the DDE client connected to the Omicron controller
        message: the ZCtrlParameters message containing the
        parameter values to be set.

    Raises:
        ParameterError if an invalid parameter value is given
        ValueError is an invalid parameter name is given
    """
    for val, attr in zip([float(message.feedbackOn), message.integralGain,
                          message.proportionalGain],
                         FEEDBACK_PARAM):
        # there are no units for feedback parameters, so no conversion needed
        set_param(client, attr, val)


def get_all_scan_params(client: DDEClient) -> list[float]:
    """Get the value of all scan parameters as floats.

    Behaviour:
        Returns the current value of all scan parameters in Omicron units
        (nm).
        The value of Pixel is returned as a pixel count, not as the index
        that is used to set it. Note that This value later needs to be
        converted to int to conform with the format of ScanParameters2d
            TODO? convert to int here and returna  list of float and int?

    Arguments:
        client: the DDE client connected to the Omicron controller

    Returns:
        The list of parameter values in Omicron units, in the order they
        are stored in ScanParameter, as floats

    Raises:
        Exception (TODO create a type?) if:
        An error is encountered while making the request to the Anfatec
        Python interface ("SXMRemote")
        SXMRemote returns null as the parameter value
    """
    vals = []
    for param in SCAN_PARAM:
        vals.append(get_param(client, param))

    # the Anfatec controller returns the coordinates of the middle of the scan
    # area. we need to convert them to the top-left coords to conform to the
    # AFSPM convention
    vals[0] -= 0.5 * vals[2]
    vals[1] -= 0.5 * vals[2]

    # normally, Anfatec controller returns pixel value as pixels (not the weird
    # index value). if this is not the case, convert from index to pixels here:
    # vals[3] = ANFATEC_RESOLUTION[vals[3] - 1]

    return vals


def get_all_feedback_params(client: DDEClient) -> list[float]:
    """Get the value of every feedback/ZCtrl parameter as floats.

    Arguments:
        client: the DDE client connected to the Omicron controller

    Returns:
        The list of parameter values, unitless, in the order they
        are stored in FeedbackParameter, as floats

    Note: 'Enable' need to be converted to bool before being sent as part of
    a ZCtrlParameter message (we currently do so in controller.py)

    Raises:
        Exception (TODO create a type?) if:
            An error is encountered while making the request to the Anfatec
            Python interface ("SXMRemote")
            SXMRemote returns null as the parameter value
    """
    param_values = []
    for param in FEEDBACK_PARAM:
        param_values.append(get_param(client, param))

    return param_values
