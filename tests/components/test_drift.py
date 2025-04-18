"""Test drift estimation logic."""

import logging
import pytest
import datetime as dt
import numpy as np

from matplotlib import pyplot as plt

from pathlib import Path
from os import sep

from afspm.components import drift

import SciFiReaders as sr
from afspm.utils import array_converters as conv


logger = logging.getLogger(__name__)


BASE_PATH = str(Path(__file__).parent.parent.resolve())


@pytest.fixture
def sample1_fname():
    return BASE_PATH + sep + 'data' + sep + 'Au_facetcontac0000.ibw'


@pytest.fixture
def sample2_fname():
    return BASE_PATH + sep + 'data' + sep + 'Au_facetcontac0003.ibw'


@pytest.fixture
def dt1():
    return dt.datetime(2025, 1, 1)


@pytest.fixture
def dt2():
    return dt.datetime(2025, 1, 1, second=1)


@pytest.fixture
def expected_trans_pix():
    return [4, 13]


@pytest.fixture
def expected_drift_vec():
    return [-6e-08, -2e-07]


@pytest.fixture
def min_pix_residual():
    return 5.0


@pytest.fixture
def min_unit_residual():
    return 1e-07


def test_transform(sample1_fname, sample2_fname, dt1, dt2,
                   expected_trans_pix, expected_drift_vec,
                   min_pix_residual, min_unit_residual,
                   monkeypatch):
    # Avoid plt.show() happening
    monkeypatch.setattr(plt, 'show', lambda: None)

    reader = sr.IgorIBWReader(sample1_fname)
    ds1 = list(reader.read(verbose=False).values())
    scan1 = conv.convert_sidpy_to_scan_pb2(ds1[0])
    da1 = conv.convert_scan_pb2_to_xarray(scan1)

    reader = sr.IgorIBWReader(sample2_fname)
    ds2 = list(reader.read(verbose=False).values())
    scan2 = conv.convert_sidpy_to_scan_pb2(ds2[0])
    da2 = conv.convert_scan_pb2_to_xarray(scan2)

    descriptor_types = [d for d in drift.DescriptorType]
    transform_types = [t for t in drift.TransformType]
    fitting_methods = [f for f in drift.FittingMethod]

#    for (d, t, f) in zip(descriptor_types, transform_types, fitting_methods):
    for descriptor in descriptor_types:
        for transform in transform_types:
            for fitting in fitting_methods:
                logger.info(f'Estimating transform for {descriptor}, '
                            f'{transform}, {fitting}')
                model = drift.create_drift_model(descriptor_type=descriptor,
                                                 transform_type=transform,
                                                 fitting=fitting)

                mapping = drift.estimate_transform(model, da1, da2,
                                                   display_fit=True)
                plt.show()

                logger.info(f'Mapping: R: {mapping.rotation}, '
                            f't: {mapping.translation}, ')
                if hasattr(mapping, 'scale'):
                    logger.info(f's: {mapping.scale}')

                residual = np.linalg.norm(
                    abs(mapping.translation - expected_trans_pix))
                logger.info(f'Pix residual: {residual}')

                # Only validate for special cases
                if (transform == drift.TransformType.EUCLIDEAN and
                    fitting == drift.FittingMethod.RANSAC and
                    descriptor in [drift.DescriptorType.SIFT,
                                   drift.DescriptorType.BRIEF]):
                    assert residual < min_pix_residual

                unit_trans, units = drift.get_translation(da2, mapping)
                logger.info(f'Unit trans: {unit_trans} [{units}]')

                drift_vec, units = drift.get_drift_vec(da2, mapping, dt1, dt2)
                logger.info(f'Drift Vec: {drift_vec} [{units}]')

                residual = np.linalg.norm(abs(drift_vec - expected_drift_vec))
                logger.info(f'Drift residual: {residual}')

                # Only validate for special cases
                if (transform == drift.TransformType.EUCLIDEAN and
                    fitting == drift.FittingMethod.RANSAC and
                    descriptor in [drift.DescriptorType.SIFT,
                                   drift.DescriptorType.BRIEF]):
                    assert residual < min_unit_residual
