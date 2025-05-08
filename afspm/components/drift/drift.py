"""Holds logic for drift estimation. Can be used to set drift vec."""

from enum import Enum
from dataclasses import dataclass
import datetime as dt
import logging

import numpy as np
import xarray as xr


from skimage import transform
from skimage import feature
from skimage import exposure
from skimage.feature.util import DescriptorExtractor
from skimage.measure import ransac

from matplotlib import pyplot as plt


logger = logging.getLogger(__name__)


class DescriptorType(str, Enum):
    """Descriptor type used to match keypoints between scans."""

    SIFT = 'SIFT'  # Gold standard.
    BRIEF = 'BRIEF'  # Faster, decent results.
    ORB = 'ORB'  # Not recommended, poor results.


class BRIEFFeatureDescriptorExtractor:
    """BRIEF FeatureDetector/DescriptorExtractor using Corner for features.

    BRIEF is a DescriptorExtractor; it is missing a FeatureDetector. To work
    equivalently to SIFT/ORB, we here create a combo that uses Harris corner
    detection and corner_peaks() to detect features.

    Attributes:
        keypoints: extracted keypoints.
        descriptors: descriptors associated to extracted keypoints.
        extractor: BRIEF DescriptorExtractor.
        corner_harris_kwargs: dict of kwargs to feed to corner_harris().
        corner_peaks_kwargs: dict of kwargs to feed to corner_peaks().
    """

    def __init__(self, extractor_kwargs: dict = dict(),
                 corner_harris_kwargs: dict = dict(),
                 corner_peaks_kwargs: dict = dict()):
        """Construct our FeatureExtractor + DescriptorExtractor combo."""
        self.keypoints = None
        self.descriptors = None
        self.extractor = feature.BRIEF(**extractor_kwargs)
        self.corner_harris_kwargs = corner_harris_kwargs
        self.corner_peaks_kwargs = corner_peaks_kwargs

    def detect_and_extract(self, arr: np.array):
        """Compute for FeatureDetector/DescriptorExtractor combo."""
        keypoints = feature.corner_peaks(
            feature.corner_harris(arr, **self.corner_harris_kwargs),
            **self.corner_peaks_kwargs)
        self.extractor.extract(arr, keypoints)
        self.keypoints = keypoints[self.extractor.mask]
        self.descriptors = self.extractor.descriptors


def _create_descriptor_extractor(
        descriptor_type: DescriptorType = DescriptorType.SIFT,
        **kwargs) -> DescriptorExtractor:
    """Create our descriptor extractor based on provided DescriptorType."""
    match descriptor_type:
        case DescriptorType.SIFT:
            return feature.SIFT(**kwargs)
        case DescriptorType.ORB:
            return feature.ORB(**kwargs)
        case DescriptorType.BRIEF:
            return BRIEFFeatureDescriptorExtractor(**kwargs)


class TransformType(str, Enum):
    """Transform we are estimating when matching."""

    EUCLIDEAN = 'EUCLIDEAN'  # R, t
    SIMILARITY = 'SIMILARITY'  # R, t, scale
    AFFINE = 'AFFINE'  # R, t, scale, shear


def _create_transform(transform_type: TransformType = TransformType.AFFINE,
                      dimensionality: int = 2
                      ) -> transform.ProjectiveTransform:
    """Create our Transform based on provided TransformType."""
    match transform_type:
        case TransformType.EUCLIDEAN:
            return transform.EuclideanTransform(dimensionality=dimensionality)
        case TransformType.SIMILARITY:
            return transform.SimilarityTransform(dimensionality=dimensionality)
        case TransformType.AFFINE:
            return transform.AffineTransform(dimensionality=dimensionality)


class FittingMethod(str, Enum):
    """Fitting method for the keypoint pairs."""

    LEAST_SQUARES = 'LeastSquares'
    RANSAC = 'RANSAC'


@dataclass
class DriftModel:
    """Holds classes needed for drift estimation."""

    descriptor_extractor: DescriptorExtractor
    match_descriptor_kwargs: dict
    transform:  transform.ProjectiveTransform
    fitting: FittingMethod
    fitting_kwargs:  dict


# These defaults are certainly good for SIFT descriptors (it's what was
# found experimentally in the original SIFT paper), and seems to do
# pretty well for the other descriptors.
DEFAULT_MATCHING_KWARGS = {'max_ratio': 0.8,
                           'cross_check': True}

# Defaults for RANSAC. We choose these because:
#     min_samples: is the minimum number of samples needed to fit our model.
#         Since our model is a plane, we need at least 3 points.
#     residual_threshold: the accepted residual between our computed points
#     (f(x)) and the true points (y). Since our spatial XY data is indexed data
#     here, we are simply allowing a reasonably large threshold of 5 pixels.
DEFAULT_RANSAC_PLANE_KWARGS = {'min_samples': 3,
                               'residual_threshold': 5.0}


def create_drift_model(descriptor_type: DescriptorType = DescriptorType.SIFT,
                       descriptor_kwargs: dict = dict(),
                       match_descriptor_kwargs: dict = DEFAULT_MATCHING_KWARGS,
                       transform_type: TransformType = TransformType.EUCLIDEAN,
                       dimensionality: int = 2,  # (x,y) points
                       fitting: FittingMethod = FittingMethod.RANSAC,
                       fitting_kwargs: dict = DEFAULT_RANSAC_PLANE_KWARGS
                       ) -> DriftModel:
    """Create a drift model from provided params.

    Args:
        descriptor_type: the descriptor type used for extracting features and
            estimating descriptors from them. Chosen from DescriptorType.
            Defaults to SIFT.
        descriptor_kwargs: dict of keyword args to feed to the DescriptorType
            constructor. Defaults to an empty dict.
        match_descriptor_kwargs: dict of keyword args to feed to the method
            match_descriptors(). By default, we use a dict that should work
            well for all DescriptorTypes.
        transform_type: the TransformType we are estimating. Defaults to
            Euclidean (i.e. only R and t).
        dimensionality: dimensionality of spatial points. Defaults to 2,
            since the data is likely (x, y) points.
        fitting: FittingMethod used to estimate the transformation. Defaults
            to FittingMethod.RANSAC, for more robust results.
        fitting_kwargs: the dict of keyword args to feed to the fitting method.
            Defaults to a good set of defaults for RANSAC. Note that these
            kwargs are only really used for the RANSAC approach.

    Returns:
        The created DriftModel.
    """
    descriptor_extractor = _create_descriptor_extractor(descriptor_type,
                                                        **descriptor_kwargs)
    transform = _create_transform(transform_type, dimensionality)
    return DriftModel(descriptor_extractor=descriptor_extractor,
                      match_descriptor_kwargs=match_descriptor_kwargs,
                      transform=transform, fitting=fitting,
                      fitting_kwargs=fitting_kwargs)


def estimate_transform(model: DriftModel,
                       da1: xr.DataArray, da2: xr.DataArray,
                       display_fit: bool = False, cmap: str | None = None
                       ) -> transform.ProjectiveTransform:
    """Estimate transform between two DataArrays.

    Given a DriftModel, estimate the transform to be performed to da2 so that
    it is aligned with da1.

    Note: if using this to estimate drift, it is important to ensure the
    spatial resolution of both images is equivalent (i.e. the dx and dy of the
    spatial dimensions are the same).

    Args:
        model: DriftModel used to estimate transform.
        da1: First DataArray.
        da2: Second DataArray.
        display_fit: whether or not to create a matplotlib fig displaying
            the calculations and fit. Defaults to False.
        cmap: the colormap to use when displaying da1 and da2 (after the
            transformation). Defaults to None (which means a suitable default
            will be chosen by matplotlib).

    Returns:
        ProjectiveTransform estimated.
    """
    # Scale intensity
    arrs = [exposure.rescale_intensity(da.as_numpy()) for da in [da1, da2]]

    # Get keypoints and descriptors [da1 == l, da2 == r (in this logic)]
    keypoints_lr = []
    descriptors_lr = []
    for arr in arrs:
        model.descriptor_extractor.detect_and_extract(arr)
        keypoints_lr.append(model.descriptor_extractor.keypoints)
        descriptors_lr.append(model.descriptor_extractor.descriptors)

    # Get matches of keypoints on both images
    matches = feature.match_descriptors(descriptors_lr[0],
                                        descriptors_lr[1],
                                        **model.match_descriptor_kwargs)
    points_lr = []
    for (keypoints, idx) in zip(keypoints_lr, range(0, 2)):
        matched_keypoints = keypoints[matches[:, idx]]
        # keypoints and descriptors are (y, x), we need them in (x. y)!
        matched_keypoints = np.fliplr(matched_keypoints)
        points_lr.append(matched_keypoints)

    # Estimate transform from keypoint matches
    match model.fitting:
        case FittingMethod.RANSAC:
            assert isinstance(model.fitting_kwargs, dict)

            def get_model():  # ugly method needed to run ransac
                return model.transform

            model.transform, inliers = ransac(
                (points_lr[0], points_lr[1]), get_model,
                **model.fitting_kwargs)
            inlier_matches = matches[inliers]
            outliers = inliers == False
            outlier_matches = matches[outliers]
        case FittingMethod.LEAST_SQUARES:
            model.transform.estimate(points_lr[0], points_lr[1])
            inlier_matches = matches
            outlier_matches = np.array(())  # Empty array
        case _:
            msg = 'An unsupported FittingMethod was chosen.'
            logger.error(msg)
            raise AttributeError(msg)

    if display_fit:
        display_estimated_transform(da1, da2, keypoints_lr, model.transform,
                                    inlier_matches, outlier_matches,
                                    cmap)

    return model.transform


def display_estimated_transform(da1: xr.DataArray, da2: xr.DataArray,
                                keypoints_lr: [np.ndarray],
                                mapping: transform.ProjectiveTransform,
                                inlier_matches: np.ndarray,
                                outlier_matches: np.ndarray,
                                cmap: str):
    """Visualize the estimated transform.

    Args:
        da1: First DataArray.
        da2: Second DataArray.
        keypoints_lr: tuple of keypoints found between da1 and da2.
        mapping: projective transform estimated.
        inlier_matches: indices of corresponding matches in first and second
            set of descriptors.
        outlier_matches: indices of thrown out matches in first and second
            set of descriptors. If no outliers were found, it should be an
            empty np.ndarray.
        cmap: str of colormap to be used to perform imshow() on da1 and the
            warped da2.
    """
    mosaic = """CAAD
                CBBD"""
    fig = plt.figure(layout='constrained')
    axd = fig.subplot_mosaic(mosaic)

    # Plot inliers (colored)
    feature.plot_matched_features(da1, da2,
                                  keypoints0=keypoints_lr[0],
                                  keypoints1=keypoints_lr[1],
                                  matches=inlier_matches,
                                  ax=axd['A'], only_matches=True)
    axd['A'].set_title('Matched Inliers (used to compute transform)')

    # Plot outliers (red)
    feature.plot_matched_features(da1, da2,
                                  keypoints0=keypoints_lr[0],
                                  keypoints1=keypoints_lr[1],
                                  matches=outlier_matches,
                                  ax=axd['B'], only_matches=True,
                                  matches_color='tab:red')
    axd['B'].set_title('Matched Outliers (rejected)')

    axd['C'].imshow(da1, cmap=cmap)
    axd['C'].set_title('Image 1')
    da2_warped = transform.warp(da2, mapping)
    axd['D'].imshow(da2_warped, cmap=cmap)
    axd['D'].set_title('Image 2 - After Warp')

    # Draw translation vector
    pix_trans = mapping.inverse.translation
    logger.warning(f'pix_trans: {pix_trans}')
    logger.warning(f'matrix: {mapping.inverse.params}')
    mid_pt = np.array([int(da2.shape[0] / 2), int(da2.shape[1] / 2)])

    # In this mode, we feed (x,y) and (u, v), for (x, x+u, y, y+v).
    axd['D'].quiver(mid_pt[0], mid_pt[1], pix_trans[0], pix_trans[1],
                    angles='xy', scale_units='xy', scale=1)


def get_translation(da: xr.DataArray,
                    mapping: transform.ProjectiveTransform
                    ) -> (list[float], str):
    """Get unit-based translation vector from a computed mapping.

    Args:
        da: Second DataArray, for which we estimated the mapping to transform
            it to a first DataArray (not fed as input).
        mapping: the ProjectiveTransform computed to transform a da2 to a da1.

    Returns:
        ([float], str) tuple of:
        - (x,y) translation vector.
        - units of said vector (only spatial units).
    """
    pix_trans = mapping.translation
    dxdy = np.array([da.y[1] - da.y[0],
                     da.x[1] - da.x[0]])
    units = da.x.attrs['units']
    unit_trans = dxdy * pix_trans

    return unit_trans, units


def get_drift_vec(da: xr.DataArray,
                  mapping: transform.ProjectiveTransform,
                  dt1: dt.datetime, dt2: dt.datetime
                  ) -> ([float], str):
    """Calculate drift vector for a DataArray given computed mapping.

    To convert from protobuf Timestamp to Datetime:
        ts.ToDatetime()

    Args:
        da: Second DataArray, for which we estimated the mapping to transform
            it to a first DataArray (not fed as input).
        mapping: the ProjectiveTransform computed to transform a da2 to a da1.
        dt1: datetime associated to da1.
        dt2: datetime associated to da2.

    Returns:
        ([float], str) tuple of:
        - (x,y) drift vector.
        - units of said vector (spatial units over time).
    """
    diff_s = (dt2 - dt1).total_seconds()

    # To get the drift vector, we need to use the *inverse* of the
    # estimated transform (which tells us how to convert the later array
    # to the original array).
    unit_trans, units = get_translation(da, mapping.inverse)

    unit_trans /= diff_s
    units = units + ' / s'

    return unit_trans, units
