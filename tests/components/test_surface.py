"""Test surface estimation logic."""
# TODO: Finish testing me!

import logging
import pytest
from typing import Callable

import numpy as np
from sklearn.preprocessing import PolynomialFeatures

from afspm.components import surface

from matplotlib import pyplot as plt


logger = logging.getLogger(__name__)


# Polynomial feature shape is [1, a, b, a^2, ab, b^2, ...]
@pytest.fixture
def plane_surface_coeffs():
    return [2, 0.2, 0.1]


@pytest.fixture
def poly_2nd_order_surface_coeffs():
    return [2, 0.2, 0.1, -0.05, 0, 0]


@pytest.fixture
def x_dim():
    return np.arange(0, 3, 0.1)


@pytest.fixture
def y_dim():
    return np.arange(0, 3, 0.1)


@pytest.fixture
def texture_kwargs():
    return {'width': 0.2, 'scale': 0.2, 'ax': 0}


@pytest.fixture
def noise_mean():
    return 0


@pytest.fixture
def noise_sigma():
    return 0.02


@pytest.fixture
def min_mse_plane():
    return 0.013  # TODO: investigate further, you didn't really test this


@pytest.fixture
def min_mse_poly():
    return 0.025   # TODO: investigate further, you didn't really test this


def step_train(yx: np.array, width: float = 1.0,
               scale: float = 1.0, ax: int = 0) -> float:
    """Outputs y value of a 'step train' function for position X.

    Args:
        yx: (y, x) position for which we output the y value.
        width: step width. Default is 1.0.
        scale: scale of y value. Default is 1.0.
        ax: axis upon which the step train happens. Default is 0 (y axis).
    """
    return int((yx[ax] / width) % 2) * scale


def create_Xy(x_dim: np.array, y_dim: np.array, poly_degree: int,
              surface_coeffs: [float],
              texture_fn: Callable | None = None,
              texture_fn_kwargs: dict | None = None,
              noise_mean: float | None = None, noise_sigma: float | None = None
              ) -> (np.array, np.array):
    """Create Xy values for a test."""
    surface_coeffs = np.array(surface_coeffs)
    X = np.array([[y, x] for y in y_dim for x in x_dim])

    # Create surface for X
    poly = PolynomialFeatures(degree=poly_degree)
    poly_x = poly.fit_transform(X)

    surface_y = poly_x @ surface_coeffs

    y = surface_y
    if texture_fn is not None and texture_fn_kwargs is not None:
        texture_y = np.array([texture_fn(x, **texture_fn_kwargs) for x in X])
        y += texture_y
    if noise_sigma is not None and noise_mean is not None:
        noise_y = noise_sigma * np.random.randn(len(texture_y)) + noise_mean
        y += noise_y

    return (X, y)


def test_surface_plane(plane_surface_coeffs, x_dim, y_dim,
                       texture_kwargs, noise_mean,
                       noise_sigma, min_mse_plane, monkeypatch):
    monkeypatch.setattr(plt, 'show', lambda: None)

    __, gt_surf_y = create_Xy(x_dim, y_dim, 1, plane_surface_coeffs)
    X, y = create_Xy(x_dim, y_dim, 1, plane_surface_coeffs, step_train,
                     texture_kwargs, noise_mean=noise_mean,
                     noise_sigma=noise_sigma)
    da = surface.convert_Xy_to_xarray(X, y)
    gt_surf_da = surface.convert_Xy_to_xarray(X, gt_surf_y)

    fitting_methods = [f for f in surface.FittingMethod]
    poly_degrees = range(1, 4)

    for fitting_method in fitting_methods:
        for poly_degree in poly_degrees:
            logger.info(f'Setting up model with fitting: {fitting_method}'
                        f', degree: {poly_degree}')

            model = surface.create_surface_model(poly_degree, fitting_method)
            surf_da, score = surface.fit_surface(da, model)

            surface.visualize(da, surf_da)
            plt.show()
            plt.close()

            logger.info(f'Score: {score}')

            # mse = (np.square(surf_da - gt_surf_da)).mean().to_numpy()
            # logger.info(f'MSE: {mse}')

            # if (fitting_method == surface.FittingMethod.RANSAC and
            #         poly_degree == 2):
            #     assert mse < min_mse_plane


def test_surface_poly(poly_2nd_order_surface_coeffs, x_dim, y_dim,
                      texture_kwargs, noise_mean, noise_sigma,
                      min_mse_poly, monkeypatch):
    monkeypatch.setattr(plt, 'show', lambda: None)

    __, gt_surf_y = create_Xy(x_dim, y_dim, 2, poly_2nd_order_surface_coeffs)
    X, y = create_Xy(x_dim, y_dim, 2, poly_2nd_order_surface_coeffs,
                     step_train, texture_kwargs, noise_mean=noise_mean,
                     noise_sigma=noise_sigma)
    da = surface.convert_Xy_to_xarray(X, y)
    gt_surf_da = surface.convert_Xy_to_xarray(X, gt_surf_y)

    fitting_methods = [f for f in surface.FittingMethod]
    poly_degrees = range(2, 4)

    for fitting_method in fitting_methods:
        for poly_degree in poly_degrees:
            logger.info(f'Setting up model with fitting: {fitting_method}'
                        f', degree: {poly_degree}')

            model = surface.create_surface_model(poly_degree, fitting_method)
            surf_da, score = surface.fit_surface(da, model)

            surface.visualize(da, surf_da)
            plt.show()
            plt.close()

            logger.info(f'Score: {score}')

            # mse = (np.square(surf_da - gt_surf_da)).mean().to_numpy()
            # logger.info(f'MSE: {mse}')

            # if (fitting_method == surface.FittingMethod.RANSAC and
            #         poly_degree == 3):
            #     assert mse < min_mse_poly
