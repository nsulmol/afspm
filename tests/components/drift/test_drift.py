"""Test drift estimation logic."""

import logging
import pytest
import datetime as dt
import numpy as np
import xarray as xr

from pathlib import Path
from os import sep

from matplotlib import pyplot as plt

from afspm.components.drift import drift


logger = logging.getLogger(__name__)


BASE_PATH = str(Path(__file__).parent.parent.resolve())


@pytest.fixture
def sample1_fname():
    return BASE_PATH + sep + '..' + sep + 'data' + sep + 'Au_facetcontac0000.nc'


@pytest.fixture
def sample2_fname():
    return BASE_PATH + sep + '..' + sep + 'data' + sep + 'Au_facetcontac0003.nc'


@pytest.fixture
def dt1():
    return dt.datetime(2025, 1, 1)


@pytest.fixture
def dt2():
    return dt.datetime(2025, 1, 1, second=1)


@pytest.fixture
def expected_trans_pix():
    return [13, 4]


@pytest.fixture
def expected_drift_rate():
    return [-2e-07, -6e-08]


@pytest.fixture
def min_pix_trans_residual():
    return 6.0


@pytest.fixture
def min_unit_trans_residual():
    return 1e-07


def get_data_array_from_dataset(fname: str) -> xr.DataArray:
    """Helper to get xarray DataArray from an nc file.

    The file contains a Dataset. We grab the first 'channel', i.e. the first
    DataArray.
    """
    ds = xr.open_dataset(fname)
    da = list(ds.values())[0]  # Grab first DataArray from Dataset
    return da


def test_transform_real_data(sample1_fname, sample2_fname, dt1, dt2,
                             expected_trans_pix, expected_drift_rate,
                             min_pix_trans_residual, min_unit_trans_residual,
                             monkeypatch):
    # Avoid plt.show() happening
    monkeypatch.setattr(plt, 'show', lambda: None)

    da1 = get_data_array_from_dataset(sample1_fname)
    da2 = get_data_array_from_dataset(sample2_fname)

    # Get normalized pix trans residual (for score comparison)
    norm_min_pix_trans_residual = (min_pix_trans_residual /
                                   np.linalg.norm(da2.shape))

    descriptor_types = [d for d in drift.DescriptorType]
    transform_types = [t for t in drift.TransformType]
    fitting_methods = [f for f in drift.FittingMethod]

    for descriptor_type in descriptor_types:
        for transform_type in transform_types:
            for fitting_type in fitting_methods:
                logger.info(f'Estimating transform for {descriptor_type}, '
                            f'{transform_type}, {fitting_type}')
                model = drift.create_drift_model(
                    descriptor_type=descriptor_type,
                    transform_type=transform_type,
                    fitting=fitting_type,
                    scan_res=da2.shape)

                mapping, score = drift.estimate_transform(model, da1, da2,
                                                          display_fit=True,
                                                          scale_factor=0.5)
                plt.show()
                plt.close()

                logger.info(f'Mapping: R: {mapping.rotation}, '
                            f't: {mapping.translation}, ')
                if hasattr(mapping, 'scale'):
                    logger.info(f's: {mapping.scale}')

                trans_residual = np.linalg.norm(
                    abs(mapping.translation - expected_trans_pix))
                logger.info(f'Pix trans_residual: {trans_residual}')

                # Only validate for special cases
                if (transform_type == drift.TransformType.EUCLIDEAN and
                    fitting_type == drift.FittingMethod.RANSAC and
                    descriptor_type in [drift.DescriptorType.SIFT,
                                        drift.DescriptorType.BRIEF]):
                    assert score < norm_min_pix_trans_residual
                    assert trans_residual < min_pix_trans_residual

                unit_trans, units = drift.get_translation(da2, mapping)
                logger.info(f'Unit trans: {unit_trans} [{units}]')

                drift_rate, units = drift.get_drift_rate(da2, mapping, dt1, dt2)
                logger.info(f'Drift Rate: {drift_rate} [{units}]')

                trans_residual = np.linalg.norm(abs(drift_rate -
                                                    expected_drift_rate))
                logger.info(f'Drift trans_residual: {trans_residual}')

                # Only validate for special cases
                if (transform_type == drift.TransformType.EUCLIDEAN and
                    fitting_type == drift.FittingMethod.RANSAC and
                    descriptor_type in [drift.DescriptorType.SIFT,
                                        drift.DescriptorType.BRIEF]):
                    assert trans_residual < min_unit_trans_residual

    # Test big scale factor
    model = drift.create_drift_model()
    mapping, score = drift.estimate_transform(model, da1, da2,
                                              display_fit=True,
                                              scale_factor=0.25)
    plt.show()
    plt.close()

    # In this case, we expect the score to be higher than our expectation
    assert score < 2 * norm_min_pix_trans_residual


@pytest.fixture
def expected_score_simulated():
    return 1.0


def test_transform_simulated(sample1_fname, sample2_fname, dt1, dt2,
                             expected_score_simulated,
                             monkeypatch):
    # Avoid plt.show() happening
    monkeypatch.setattr(plt, 'show', lambda: None)

    da1 = get_data_array_from_dataset(sample1_fname)

    # Testing translation in x, translation in y, then translation in both.
    tl_xs = [da1.x[0] - 0.5*(da1.x[-1] - da1.x[0]),
             da1.x[0],
             da1.x[0] - 0.5*(da1.x[-1] - da1.x[0])]
    tl_ys = [da1.y[0],
             da1.y[0] - 0.5*(da1.y[-1] - da1.y[0]),
             da1.y[0] - 0.5*(da1.y[-1] - da1.y[0])]
    x_ranges = [int(1.5*da1.x.shape[0]),
                da1.x.shape[0],
                int(1.5*da1.x.shape[0])]
    y_ranges = [da1.y.shape[0],
                int(1.5*da1.y.shape[0]),
                int(1.5*da1.y.shape[0])]

    for (tl_x, tl_y, x_range, y_range) in zip(tl_xs, tl_ys,
                                              x_ranges, y_ranges):
        x2 = np.linspace(tl_x, da1.x[-1], x_range)
        y2 = np.linspace(tl_y, da1.y[-1], y_range)
        da2 = da1.interp(x=x2, y=y2)

        model = drift.create_drift_model()
        mapping, score = drift.estimate_transform(model, da1, da2,
                                                  display_fit=True,
                                                  scale_factor=0.5)
        plt.show()
        plt.close()

        # Get normalized expected score (for score comparison)
        norm_expected_score = (expected_score_simulated /
                               np.linalg.norm(da2.shape))

        assert score < norm_expected_score
