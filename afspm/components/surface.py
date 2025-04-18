"""Holds logic for fitting surfaces, subtracting them, setting feedback slope.

REMINDER: matrices are (row, col), so (y, x). This is why we do yx coords
throughout.

How to use:
    import afspm.utils.array_converters as ac
    import afspm.components.surface as surface

    model = surface.create_surface_model(...)

    # Fit data to model
    da = ac.convert_scan_pb2_to_xarray(scan)
    (X, y) = convert_xarray_to_Xy(da)
    model.fit()

    # Create DataArray of surface
    surf_y = model.predict(X)
    surf_y = surface_y.reshape(da.shape)
    surf_da = da.copy(data=surf_y)

    # Get true deviation scan (removing background)
    true_data = da - surf_da
"""

from enum import Enum
import logging

import numpy as np
import xarray as xr
from matplotlib.axes import Axes
from matplotlib.colors import Colormap
from matplotlib import pyplot as plt

from sklearn.base import BaseEstimator
from sklearn.linear_model import (
    HuberRegressor,
    LinearRegression,
    RANSACRegressor,
    TheilSenRegressor,
    QuantileRegressor
)

from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import PolynomialFeatures, StandardScaler

from afspm.io.protos.generated import scan_pb2


logger = logging.getLogger(__name__)


class FittingMethod(str, Enum):
    """Fitting method used to fit the surface.

    If curious about the various methods, please consult sklearn and
    their Supervised Learning - Linear Regression section (1.1).
    """

    LEAST_SQUARES = 'LeastSquares'
    THEIL_SEN = 'Theil-Sen'
    RANSAC = 'RANSAC'
    HUBER = 'Huber'
    QUANTILE = 'Quantile'  # Defaults to median regressor (i.e. MAE)


# Quantile parameter
LASSO_KEY = 'alpha'


def _create_estimator(method: FittingMethod = FittingMethod.LEAST_SQUARES,
                      **kwargs) -> BaseEstimator:
    """Create simple estimator based on provided method and kwargs."""
    match method:
        case FittingMethod.LEAST_SQUARES:
            return LinearRegression(**kwargs)
        case FittingMethod.THEIL_SEN:
            return TheilSenRegressor(**kwargs)
        case FittingMethod.RANSAC:
            return RANSACRegressor(**kwargs)
        case FittingMethod.HUBER:
            return HuberRegressor(**kwargs)
        case FittingMethod.QUANTILE:
            if LASSO_KEY not in kwargs:
                # Default L1-regularization to 0 (not working without this
                # during very basic tests).
                kwargs[LASSO_KEY] = 0.0
            return QuantileRegressor(**kwargs)


def create_surface_model(polynomial_degree: int = 1,
                         method: FittingMethod = FittingMethod.LEAST_SQUARES,
                         **kwargs) -> BaseEstimator:
    """Create a linear model for fitting a sample surface.

    The fitting can later be used to, for example, subtract the background (in
    order to get better texture information, i.e. variation relative to overall
    surface).

    Args:
        polynomial_degree: the polynomial degree used to model the surface. A
            value of 1 would correspond to a linear fit, 2 a quadratic fit, 3
            a 3rd order polynomial, etc. Defaults to a linear fit (i.e. 1).
        method: the fitting method used to estimate the surface. Defaults to
            a least-squares fit.
        **kwargs: any additional arguments needed to instantiate the estimator.

    Returns:
        The BaseEstimator to be used for fitting.
    """
    estimator = _create_estimator(method, **kwargs)
    return make_pipeline(StandardScaler(),
                         PolynomialFeatures(polynomial_degree), estimator)


def convert_scan_to_Xy(scan: scan_pb2.Scan2d) -> (np.array, np.array):
    """Extract (X,y) form of provided 2D scan.

    A scan consists of a 2D region, where each position in the region contains a
    value. In order to fit a model to this, we need to convert it into a set of
    (X, y) pairs, where X is a vector of input values (in this case, the
    2D yx positions), and y is the scalar output (in this case, the value
    at each yx position). This method accomplishes that.

    NOTE: this method considers the raw data, i.e. the yx positions are
    simply indices, and there are no units. For a unit-ed approach,
    use convert_xarray_to_Xy().

    Args:
        scan: Scan2d containing the yx positions and y/intensity values.

    Returns:
        (X, y) tuple containing:
        - X: np.array of shape (N*M, 2), where the input scan is of size
            N*M. These are the 2D yx positions of the data points.
        - y: np.array of shape (N*M, 1), where the input scan is of size
            N*M. These are the intensity values at each position.
    """
    indices = np.indices((scan.params.data.shape.y, scan.params.data.shape.x))
    X = np.column_stack((indices[0].ravel(), indices[1].ravel()))
    y = np.array(scan.values, dtype=np.float64)
    return (X, y)


def convert_xarray_to_Xy(da: xr.DataArray) -> (np.array, np.array):
    """Extract (X,y) form of provided xarray DataArray.

    A scan consists of a 2D region, where each position in the region contains a
    value. In order to fit a model to this, we need to convert it into a set of
    (X, y) pairs, where X is a vector of input values (in this case, the
    2D yx positions), and y is the scalar output (in this case, the value
    at each yx position). This method accomplishes that.

    NOTE: This method specifically assumes:
    - x and y are labeled 'x' and 'y' in the DataArray.
    - the 'x' and 'y' coordinates are logical coordinates, i.e. their
    sizes match the size of the associated dimension.
    It will reliably work if used in conjunction with array_converters'
    convert_scan_pb2_to_xarray().

    Args:
        da: DataArray containing the yx positions and y/intensity values.

    Returns:
        (X, y) tuple containing:
        X: np.array of shape (N*M, 2), where the input scan is of size
            N*M. These are the 2D yx positions of the data points.
        y: np.array of shape (N*M, 1), where the input scan is of size
            N*M. These are the intensity values at each position.
    """
    assert len(da.y.values) == da.shape[0]
    assert len(da.x.values) == da.shape[1]

    # Reminder: da.y and da.x are 1D arrays containing the points of each
    # dimension. To convert to X, we need to have the 2D XY or YX pairs.
    coords = [[y, x] for y in da.y.values for x in da.x.values]

    X = np.array(coords)
    y = da.values.ravel()
    return (X, y)


def convert_Xy_to_xarray(X: np.array, y: np.array) -> xr.DataArray:
    """Create xarray DataArray from provided X and y values.

    NOTE: Assumes box/rectangle/raster!

    Args:
        X: np.array of shape (N*M, 2), where the input scan is of size
            N*M. These are the 2D yx positions of the data points.
        y: np.array of shape (N*M, 1), where the input scan is of size
            N*M. These are the intensity values at each position.
    Returns:
        DataArray
    """
    X0 = np.unique(X[:, 0])
    X1 = np.unique(X[:, 1])

    assert len(X0) * len(X1) == X.shape[0]

    y = np.reshape(y, (len(X0), len(X1)))
    return xr.DataArray(data=y, dims=['y', 'x'],
                        coords={'y': X0, 'x': X1})


def update_xarray_y(da: xr.DataArray, y: np.array) -> xr.DataArray:
    """Update da with new 'y' data (intensity values).

    Args:
        da: DataArray containing original yx positions and y/intensity values.
        y: np.array of shape (N*M, 1), where the input scan is of size
            N*M. These are the intensity values at each position.
    Returns:
        DataArray with y/intensity values updated.
    """
    return da.copy(data=y)


def fit_surface(da: xr.DataArray, model: BaseEstimator
                ) -> (xr.DataArray, float):
    """Fit a surface model to data.

    Args:
        da: DataArray of a microscope scan.
        model: BaseEstimator used to fit surface.

    Returns:
        (xr.DataArray, float) tuple containing:
        - A copy of the DataArray, with values representing the surface.
        - Score value of fitting.
    """
    # Fit data to model
    (X, y) = convert_xarray_to_Xy(da)
    model.fit(X, y)
    score = model.score(X, y)

    # Create DataArray of surface
    surf_y = model.predict(X)
    surf_y = surf_y.reshape(da.shape)
    surf_da = da.copy(data=surf_y)
    return surf_da, score


def visualize(original_da: xr.DataArray, surface_da: xr.DataArray,
              ax: Axes | None = None,
              cmap: Colormap | str | None = None):
    """Visualize the estimated surface relative to the original data.

    Args:
        original_da: DataArray of a microscope scan.
        surface_da: DataArray of the estimated surface.
        ax: Axes on which to plot. By default, use current axes.
        cmap: Mapping from data values to color space. Default is None,
            which uses an internal plt default.
    """
    if ax is None:
        fig = plt.figure()
        ax = fig.add_subplot(projection='3d')

    surface_da.plot.surface(ax=ax, cmap=cmap)

    X, y = convert_xarray_to_Xy(original_da)
    ax.scatter(X[:, 1], X[:, 0], y, color='orange')
