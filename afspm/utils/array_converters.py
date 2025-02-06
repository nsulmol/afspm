"""Array converter helpers.

Note that the conversions done know nothing about the angle that the
data was collected with! Unfortunately, most readers provide coordinate/
physical dimension data in 1D, so the data does not account for rotations
performed. Instead, the rotation angle is usually stored as a metadata
attribute (via the 'original_metadata' parameter).

As such, the physical coordinate data is not correct if an angle was used,
and must be corrected. Please ensure you populate scan_pb2.Scan2d's angle
parameter so this information is available to other components/methods that
might fix it.
"""

import logging
import xarray as xr
import numpy as np
import imageio.v3 as iio
from ..io.protos.generated import scan_pb2
from ..io.protos.generated import geometry_pb2


logger = logging.getLogger(__name__)


try:
    import sidpy
    from sidpy.sid import Dataset
except ModuleNotFoundError:
    logger.warning("You don't have sidpy installed. "
                   "If you wish to use this method, you will need to install "
                   "it (pip install sidpy) before attempting.")
    sidpy = None
    Dataset = None


def convert_scan_pb2_to_xarray(scan: scan_pb2.Scan2d) -> xr.DataArray:
    """Convert protobuf Scan message to xarray Dataset.

    Args:
        scan: protobuf Scan message

    Returns:
        xarray DataArray instance.

    Raises:
        None.
    """
    roi = scan.params.spatial.roi
    x = np.linspace(roi.top_left.x, roi.top_left.x + roi.size.x,
                    scan.params.data.shape.x)
    y = np.linspace(roi.top_left.y, roi.top_left.y + roi.size.y,
                    scan.params.data.shape.y)
    data = np.array(scan.values, dtype=np.float64)
    data = data.reshape((scan.params.data.shape.y,
                         scan.params.data.shape.x))

    da = xr.DataArray(data=data, dims=['y', 'x'],
                      coords={'y': y, 'x': x},
                      attrs={'units': scan.params.data.units})
    da.x.attrs['units'] = scan.params.spatial.units
    da.y.attrs['units'] = scan.params.spatial.units
    da.name = scan.channel
    return da


def convert_xarray_to_scan_pb2(da: xr.DataArray) -> scan_pb2.Scan2d:
    """Convert protobuf Scan message to xarray Dataset.

    Args:
        data_array: xarray DataArray instance.

    Returns:
        protobuf Scan message.

    Raises:
        None.
    """
    da_shape = geometry_pb2.Size2u(x=da.shape[0],
                                   y=da.shape[1])

    tl = {}
    size = {}
    for dim in da.dims:
        key = 'x' if 'x' in dim else 'y'
        tl[key] = da[dim].min().item()
        size[key] = da[dim].max().item() - da[dim].min().item()
    top_left = geometry_pb2.Point2d(**tl)
    size = geometry_pb2.Size2d(**size)
    roi = geometry_pb2.RotRect2d(top_left=top_left, size=size)
    physical_units = (da[da.dims[0]].units if 'units' in da[da.dims[0]].attrs
                      else None)
    spatial_aspects = scan_pb2.SpatialAspects(roi=roi,
                                              units=physical_units)
    data_units = da.units if 'units' in da.attrs else None
    data_aspects = scan_pb2.DataAspects(shape=da_shape, units=data_units)
    scan_params = scan_pb2.ScanParameters2d(spatial=spatial_aspects,
                                            data=data_aspects)
    scan = scan_pb2.Scan2d(params=scan_params,
                           channel=da.name,
                           values=da.values.ravel().tolist())
    return scan


def convert_scan_pb2_to_sidpy(scan: scan_pb2.Scan2d) -> Dataset:
    """Convert protobuf Scan message to sidpy Dataset.

    Args:
        scan: protobuf Scan message

    Returns:
        sidpy Dataset instance.

    Raises:
        None.
    """
    if not sidpy:
        raise ModuleNotFoundError("sidpy is required for this method.")

    roi = scan.params.spatial.roi
    x = np.linspace(roi.top_left.x, roi.top_left.x + roi.size.x,
                    scan.params.data.shape.x)
    y = np.linspace(roi.top_left.y, roi.top_left.y + roi.size.y,
                    scan.params.data.shape.y)
    data = np.array(scan.values, dtype=np.float64)
    data = data.reshape((scan.params.data.shape.y,
                         scan.params.data.shape.x))

    dset = sidpy.Dataset.from_array(data)
    dset.data_type = 'image'
    dset.units = scan.params.data.units

    dset.set_dimension(0, sidpy.Dimension(x, 'x'))
    dset.set_dimension(1, sidpy.Dimension(y, 'y'))

    for dim in [dset.x, dset.y]:
        dim.dimension_type = 'spatial'
        dim.quantity = 'distance'
        dim.units = scan.params.spatial.units

    dset.quantity = scan.channel
    return dset


def convert_sidpy_to_scan_pb2(ds: Dataset) -> scan_pb2.Scan2d:
    """Convert sidpy Dataset to protobuf Scan message.

    Args:
        dataset: sidpy Dataset instance.

    Returns:
        protobuf Scan2d message

    Raises:
        None.
    """
    if not sidpy:
        raise ModuleNotFoundError("sidpy is required for this method.")

    da_shape = geometry_pb2.Size2u(x=ds.shape[0],
                                   y=ds.shape[1])
    tl = {}
    size = {}
    for dim in [ds.x, ds.y]:
        key = 'x' if 'x' in dim.name else 'y'
        tl[key] = dim.min().item()
        size[key] = dim.max().item() - dim.min().item()
    top_left = geometry_pb2.Point2d(**tl)
    size = geometry_pb2.Size2d(**size)
    roi = geometry_pb2.RotRect2d(top_left=top_left, size=size)
    spatial_aspects = scan_pb2.SpatialAspects(roi=roi,
                                              units=ds.x.units)
    data_aspects = scan_pb2.DataAspects(shape=da_shape, units=ds.units)
    scan_params = scan_pb2.ScanParameters2d(spatial=spatial_aspects,
                                            data=data_aspects)

    scan = scan_pb2.Scan2d(params=scan_params,
                           channel=ds.quantity,
                           values=ds.compute().ravel().tolist())
    return scan


def create_xarray_from_img_path(img_path: str,
                                tl: tuple[float, float] = None,
                                size: tuple[float, float] = None,
                                physical_units: str = None,
                                data_units: str = None):
    """Create an xarray from the provided image and physical units data."""
    img = np.asarray(iio.imread(img_path))[:, :, 0]  # Grab single channel

    coords = {}
    if tl and size:
        y = np.linspace(tl[0], tl[0] + size[0], img.shape[0])  # row, col
        x = np.linspace(tl[1], tl[1] + size[1], img.shape[1])
        coords = {'y': y, 'x': x}

    attrs = {}
    if data_units:
        attrs = {'units': data_units}

    da = xr.DataArray(data=img, dims=['y', 'x'],
                      coords=coords, attrs=attrs)
    if physical_units:
        da.x.attrs['units'] = physical_units
        da.y.attrs['units'] = physical_units

    return da
