"""Validate methods in units work."""

import pytest
import pint
from numpy.testing import assert_almost_equal

import afspm.utils.units as units


@pytest.fixture
def val_nm():
    return 10.55


@pytest.fixture
def units_nm():
    return 'nm'


@pytest.fixture
def val_ang():
    return 105.5


@pytest.fixture
def units_ang():
    return 'angstrom'


@pytest.fixture
def units_fake():
    return 'banana'


def test_convert(val_nm, units_nm, val_ang, units_ang):
    """Ensure conversions are computed properly."""
    res_ang = units.convert(val_nm, units_nm, units_ang)
    assert_almost_equal(res_ang, val_ang)

    res_nm = units.convert(val_ang, units_ang, units_nm)
    assert_almost_equal(res_nm, val_nm)

    # No units, should pass the vame value.
    res_nm = units.convert(val_nm, None, None)
    assert_almost_equal(res_nm, val_nm)


def test_break(val_nm, units_nm, units_fake):
    """Ensure fake units or missing units throw errors."""
    with pytest.raises(units.ConversionError):
        units.convert(val_nm, units_nm, units_fake)
    with pytest.raises(units.ConversionError):
        units.convert(val_nm, units_nm, None)
    with pytest.raises(units.ConversionError):
        units.convert(val_nm, None, units_nm)
