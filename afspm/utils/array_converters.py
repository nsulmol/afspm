"""Array converter helpers."""

import xarray as xr
import numpy as np
from ..io.protos.generated import scan_pb2
from ..io.protos.generated import geometry_pb2

try:
    import sidpy
    from sidpy.sid import Dataset
except ModuleNotFoundError:
    print("You don't have sidpy installed. "
          "If you wish to use this method, you will need to install it "
          "(pip install sidpy) before attempting.")
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
    roi = scan.parameters.spatial_roi
    x = np.linspace(roi.top_left.x, roi.top_left.x + roi.size.x,
                    scan.parameters.data_shape.x)
    y = np.linspace(roi.top_left.y, roi.top_left.y + roi.size.y,
                    scan.parameters.data_shape.y)
    data = np.array(scan.data_array, dtype=np.float64)
    data = data.reshape((scan.parameters.data_shape.x,
                         scan.parameters.data_shape.y))

    da = xr.DataArray(data=data, dims=['y', 'x'],
                      coords={'y': y, 'x': x},
                      attrs={'units': scan.parameters.data_units})
    da.x.attrs['units'] = roi.units
    da.y.attrs['units'] = roi.units
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
    roi = geometry_pb2.Rect2d(top_left=top_left, size=size,
                              units=da[da.dims[0]].units)
    scan_params = scan_pb2.ScanParameters2d(spatial_roi=roi,
                                            data_shape=da_shape,
                                            data_units=da.units,
                                            name=da.name)

    scan = scan_pb2.Scan2d(parameters=scan_params,
                           data_array=da.values.ravel().tolist())
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

    roi = scan.parameters.spatial_roi
    x = np.linspace(roi.top_left.x, roi.top_left.x + roi.size.x,
                    scan.parameters.data_shape.x)
    y = np.linspace(roi.top_left.y, roi.top_left.y + roi.size.y,
                    scan.parameters.data_shape.y)

    data = np.array(scan.data_array, dtype=np.float64)
    data = data.reshape((scan.parameters.data_shape.x,
                         scan.parameters.data_shape.y))

    dset = sidpy.Dataset.from_array(data)
    dset.data_type = 'image'
    dset.units = scan.parameters.data_units

    dset.set_dimension(0, sidpy.Dimension(x, 'x'))
    dset.set_dimension(1, sidpy.Dimension(y, 'y'))

    for dim in [dset.x, dset.y]:
        dim.dimension_type = 'spatial'
        dim.quantity = 'distance'
        dim.units = roi.units

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
    roi = geometry_pb2.Rect2d(top_left=top_left, size=size,
                              units=da[da.dims[0]].units)
    scan_params = scan_pb2.ScanParameters2d(spatial_roi=roi,
                                            data_shape=da_shape,
                                            data_units=ds.units,
                                            name=ds.name)

    scan = scan_pb2.Scan2d(parameters=scan_params,
                           data_array=ds.ravel().tolist())
    return scan



    # scan_params = scan_pb2.ScanParameters()
    # scan_params.data_shape.update({'height': ds.shape[0],
    #                                      'width': ds.shape[1],
    #                                      'depth': ds.shape[2] if
    #                                      len(ds.shape) > 2 else 1})
    # scan_params.spatial_units = ds.x.units
    # scan_params.data_units = ds.units
    # scan_params.name = ds.name

    # tl = {}
    # size = {}
    # for dim in [ds.x, ds.y]:
    #     key = 'x' if 'x' in dim.name else 'y'
    #     tl[key] = dim.min().item()
    #     size[key] = dim.max().item() - dim.min().item()
    # scan_params.top_left.update(tl)
    # scan_params.size.update(size)

    # scan = scan_pb2.Scan()
    # scan.parameters = scan_params
    # scan.data_array.extend(ds.ravel().tolist())
    # return scan
