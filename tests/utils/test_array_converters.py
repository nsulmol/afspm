"""Test array conversion logic."""

import afspm.utils.array_converters as conv
import xarray as xr
import numpy as np
from pathlib import Path
from os import sep
import pytest


TEST_IMG_PATH = (str(Path(__file__).parent.parent.resolve()) + sep + "data" +
                     sep + "sample.png")


class TestConverters:
    data = np.random.normal(loc=3, scale=2.5, size=(128, 128))
    data_units = 'm'
    x = np.linspace(-5, 5, data.shape[1])
    y = np.linspace(-10, 10, data.shape[0])
    spatial_units = 'nm'
    name = 'Topography'

    def test_convert_xarray(self):
        da = xr.DataArray(data=self.data, dims=['y', 'x'],
                          coords={'y': self.y, 'x': self.x},
                          attrs={'units': self.data_units})
        da.x.attrs['units'] = self.spatial_units
        da.y.attrs['units'] = self.spatial_units

        scan = conv.convert_xarray_to_scan_pb2(da)
        da2 = conv.convert_scan_pb2_to_xarray(scan)

        assert (da == da2).all()

    def test_convert_sidpy(self):
        sidpy = pytest.importorskip('sidpy')

        dset = sidpy.Dataset.from_array(self.data)
        dset.data_type = 'image'
        dset.units = self.data_units
        dset.set_dimension(0, sidpy.Dimension(self.x, 'x'))
        dset.set_dimension(1, sidpy.Dimension(self.y, 'y'))

        for dim in [dset.x, dset.y]:
            dim.dimension_type = 'spatial'
            dim.quantity = 'distance'
            dim.units = self.spatial_units

        scan = conv.convert_sidpy_to_scan_pb2(dset)
        dset2 = conv.convert_scan_pb2_to_sidpy(scan)

        assert dset == dset2


@pytest.fixture
def tl():
    return [-5, -2]


@pytest.fixture
def size():
    return [20, 15]


@pytest.fixture
def physical_units():
    return 'nm'


@pytest.fixture
def data_units():
    return 'banana'


def test_create_from_img(tl, size, physical_units, data_units):
    res = conv.create_xarray_from_img_path(TEST_IMG_PATH)
    assert res is not None
    for val in ('x', 'y'):
        assert val in res.dims
    assert not res.coords
    assert not res.attrs

    res = conv.create_xarray_from_img_path(TEST_IMG_PATH,
                                           tl=tl,
                                           size=size,
                                           physical_units=physical_units,
                                           data_units=data_units)
    assert res is not None
    for val in ('x', 'y'):
        assert val in res.dims
        assert val in res.coords
    assert 'units' in res.attrs and res.units == data_units
    assert 'units' in res.x.attrs and res.x.units == physical_units
