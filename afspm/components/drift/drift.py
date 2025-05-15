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


# (x, y) points
DEFAULT_DIMENSIONALITY = 2  # I'm not sure when it would be anything else...


# These defaults are certainly good for SIFT descriptors (it's what was
# found experimentally in the original SIFT paper), and seems to do
# pretty well for the other descriptors.
DEFAULT_MATCHING_KWARGS = {'max_ratio': 0.8,
                           'cross_check': True}
DEFAULT_RESIDUAL_THRESH_PERCENT = 0.05
DEFAULT_SCAN_RESOLUTION = [256, 256]
RANSAC_RESIDUAL_THRESH_KEY = 'residual_threshold'
RANSAC_MIN_SAMPLES_KEY = 'min_samples'
WORST_FIT = 1.0


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

    TRANSLATION = 'TRANSLATION'  # t
    EUCLIDEAN = 'EUCLIDEAN'  # R, t
    SIMILARITY = 'SIMILARITY'  # R, t, scale
    AFFINE = 'AFFINE'  # R, t, scale, shear


class TranslationTransform(transform.EuclideanTransform):
    """Simplest transform, we just estimate a translation."""

    def __init__(self, **kwargs):
        """Init based on EuclideanTransform."""
        super().__init__(**kwargs)

    def estimate(self, src, dst):  # We don't allow weights!
        """Estimate translation from src to dst.

        We simply do a mean on our residuals.
        """
        trans = np.mean(dst - src, axis=0)  # TODO: Return to mean
        self.params[0:2, 2] = trans
        return True


def _create_transform(transform_type: TransformType = TransformType.AFFINE,
                      dimensionality: int = DEFAULT_DIMENSIONALITY
                      ) -> transform.ProjectiveTransform:
    """Create our Transform based on provided TransformType."""
    match transform_type:
        case TransformType.TRANSLATION:
            return TranslationTransform(dimensionality=dimensionality)
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
    """Holds classes needed for drift estimation.

    NOTE:
    If using FittingMethod.RANSAC, it is important to update the drift model
    whenever the scan resolution changes. This is because some of the
    parameters in the RANSAC kwargs are pixel-based! These can be
    updated by calling update_fitting_kwargs() with the latest scan resolution.
    """

    descriptor_extractor: DescriptorExtractor
    match_descriptor_kwargs: dict
    transform:  transform.ProjectiveTransform
    fitting: FittingMethod
    fitting_kwargs:  dict

    scan_resolution: tuple[int, int]  # To determine if fitting needs updating

    def update_fitting_kwargs(self, new_scan_res: tuple[int, int]):
        """Update fitting kwargs if scan resolution has changed."""
        if new_scan_res != self.scan_resolution:
            norm_scan_res = np.linalg.norm(np.array(self.scan_resolution))
            residual_threshold_percent = (
                self.fitting_kwargs[RANSAC_RESIDUAL_THRESH_KEY] /
                norm_scan_res)
            self.fitting_kwargs[RANSAC_RESIDUAL_THRESH_KEY] = (
                _calculate_residual_threshold(
                    new_scan_res, residual_threshold_percent))


def _get_min_samples(transform_type: TransformType) -> int:
    match transform_type:
        case TransformType.TRANSLATION:
            return 2
        case _:
            return 3  # All other ones are 3


def _calculate_residual_threshold(scan_resolution: tuple[int, int],
                                  residual_threshold_percent: float) -> float:
    norm_scan_res = np.linalg.norm(np.array(scan_resolution))
    return residual_threshold_percent * norm_scan_res


def create_ransac_kwargs(transform_type: TransformType,
                         scan_resolution: tuple[int, int],
                         residual_threshold_percent: float =
                         DEFAULT_RESIDUAL_THRESH_PERCENT) -> dict:
    """Create RANSAC kwargs given transform type and residual threshold.

    This method allows you to feed your transform type and desired residual
    threshold (defining inliers vs. outliers) as a percentage of the scan
    resolution.

    Args:
        transform_type: the TransformType being used for estimation. We use
            this to determine minimum the number of points needed to fit.
        scan_resolution: (x, y) scan resolution used to convert residual
            threshold from percent to pixels.
        residual_threshold_percent: the maximum residual allowable to define
            a point as an inlier. It is provided in percent relative to the
            scan resolution.

    Returns:
        A kwargs dict of arguments to feed to the ransac estimator.
    """
    kwargs = {}
    kwargs[RANSAC_MIN_SAMPLES_KEY] = _get_min_samples(transform_type)
    kwargs[RANSAC_RESIDUAL_THRESH_KEY] = _calculate_residual_threshold(
        scan_resolution, residual_threshold_percent)
    return kwargs


def create_drift_model(descriptor_type: DescriptorType = DescriptorType.BRIEF,
                       descriptor_kwargs: dict = dict(),
                       match_descriptor_kwargs: dict = DEFAULT_MATCHING_KWARGS,
                       transform_type: TransformType = TransformType.TRANSLATION,
                       dimensionality: int = DEFAULT_DIMENSIONALITY,
                       fitting: FittingMethod = FittingMethod.RANSAC,
                       fitting_kwargs: dict | None = None,
                       scan_res: tuple[int, int] = DEFAULT_SCAN_RESOLUTION
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
        scan_res: scan resolution of input scans. This must match the
            scan resolution fed to fitting_kwargs (i.e. if you provide one
            here, you should create fitting_kwargs using
            create_fitting_kwargs()).

    Returns:
        The created DriftModel.
    """
    if fitting_kwargs is None:
        fitting_kwargs = create_ransac_kwargs(transform_type, scan_res)

    descriptor_extractor = _create_descriptor_extractor(descriptor_type,
                                                        **descriptor_kwargs)

    transform = _create_transform(transform_type, dimensionality)
    return DriftModel(descriptor_extractor=descriptor_extractor,
                      match_descriptor_kwargs=match_descriptor_kwargs,
                      transform=transform, fitting=fitting,
                      fitting_kwargs=fitting_kwargs,
                      scan_resolution=scan_res)


def estimate_transform(model: DriftModel,
                       da1: xr.DataArray, da2: xr.DataArray,
                       display_fit: bool = False,
                       figure: plt.figure = None,
                       cmap: str | None = None,
                       scale_factor: float = 1.0
                       ) -> (transform.ProjectiveTransform | None, float):
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
        figure: matplotlib figure, for displaying. Used if available.
            Defaults to None.
        cmap: the colormap to use when displaying da1 and da2 (after the
            transformation). Defaults to None (which means a suitable default
            will be chosen by matplotlib).
        scale_factor: for scaling the arrays before fitting. Can be used to
            speed up computation, at the potential cost of not matching.

    Returns:
        (transform, score), where:
        transform: ProjectiveTransform estimated. None if RANSAC is used and
            fitting failed.
        score: normalized mean-square error between points from one of the das
            and those of the other *after* preforming the transform. If RANSAC
            is used, only the inliers are considered for this estimate. The
            normalization is done relative to the resolution of da2.
            Note: this makes a score of 0 best, and a score of 1 worst!
    """
    # For computation purposes, need to change all NaN to 0
    da1 = da1.fillna(0)
    da2 = da2.fillna(0)

    # Scale intensity
    arrs = [exposure.rescale_intensity(da)
            for da in [da1, da2]]

    # Scale resolution
    arrs = [transform.rescale(arr, scale_factor) for arr in arrs]

    try:
        keypoints_lr, descriptors_lr = _get_keypoints_and_descriptors(
            model, arrs, scale_factor)
    except (ValueError, RuntimeError) as e:
        msg = ("Error getting keypoints and descriptors when estimating drift, "
               f"skipping: {e}")
        logger.warning(msg)
        return None, WORST_FIT

    try:
        matches, points_lr = _match_descriptors(model, keypoints_lr,
                                                descriptors_lr)
    except (ValueError, RuntimeError) as e:
        msg = ("Error matching descriptors when estimating drift, "
               f"skipping: {e}")
        logger.warning(msg)
        return None, WORST_FIT

    try:
        fit_transform, norm_score = _estimate_transform(
            da1, da2, model, matches, points_lr, keypoints_lr, display_fit,
            figure, cmap)
    except (ValueError, RuntimeError) as e:
        # ValueError tends to happen if there were less keypoints then the
        # minimum number of samples needed (in RANSAC).
        msg = f"Error fitting transform when estimating drift, skipping: {e}"
        logger.warning(msg)
        return None, WORST_FIT

    return fit_transform, norm_score


def _get_keypoints_and_descriptors(model: DriftModel,
                                   arrs: list[np.ndarray],
                                   scale_factor: float
                                   ) -> (list[np.ndarray], list[np.ndarray]):
    """Get keypoints and descriptors for both images."""
    keypoints_lr = []
    descriptors_lr = []
    for arr in arrs:
        model.descriptor_extractor.detect_and_extract(arr)
        keypoints_lr.append(model.descriptor_extractor.keypoints
                            / scale_factor)
        descriptors_lr.append(model.descriptor_extractor.descriptors)
    return keypoints_lr, descriptors_lr


def _match_descriptors(model: DriftModel,
                       keypoints_lr: list[np.ndarray],
                       descriptors_lr: list[np.ndarray]
                       ) -> (np.ndarray, list[np.ndarray]):
    """Get matches of keypoints on both images."""
    matches = feature.match_descriptors(descriptors_lr[0],
                                        descriptors_lr[1],
                                        **model.match_descriptor_kwargs)
    points_lr = []
    for (keypoints, idx) in zip(keypoints_lr, range(0, 2)):
        matched_keypoints = keypoints[matches[:, idx]]
        # keypoints and descriptors are (y, x), we need them in (x. y)!
        matched_keypoints = np.fliplr(matched_keypoints)
        points_lr.append(matched_keypoints)
    return matches, points_lr


def _estimate_transform(da1: xr.Dataset, da2: xr.Dataset,
                        model: DriftModel,
                        matches: np.ndarray,
                        points_lr: list[np.ndarray],
                        keypoints_lr: list[np.ndarray],
                        display_fit: bool,
                        figure: plt.figure,
                        cmap: str | None,
                        ) -> (transform.ProjectiveTransform, float):
    """Estimate transform from keypoint matches."""
    success = True  # Assume model fitting succeeded
    match model.fitting:
        case FittingMethod.RANSAC:
            assert isinstance(model.fitting_kwargs, dict)

            def get_model():  # ugly method needed to run ransac
                return model.transform

            # Ensure proper resolution for RANSAC arguments
            model.update_fitting_kwargs(da2.shape)
            fit_transform, inliers = ransac(
                (points_lr[0], points_lr[1]), get_model,
                **model.fitting_kwargs)

            # If fitting fails, fit_transform is set to None
            success = fit_transform is not None

            inlier_points_lr = [points_lr[0][inliers], points_lr[1][inliers]]
            inlier_matches = matches[inliers]
            outliers = inliers == False
            outlier_matches = matches[outliers]
        case FittingMethod.LEAST_SQUARES:
            success = model.transform.estimate(points_lr[0], points_lr[1])
            fit_transform = model.transform
            inlier_points_lr = points_lr
            inlier_matches = matches
            outlier_matches = np.array(())  # Empty array
        case _:
            msg = 'An unsupported FittingMethod was chosen.'
            logger.error(msg)
            raise AttributeError(msg)

    if success:
        # Score is error between estimated points after transforming one da
        # to the other, and the actual points.
        score = np.mean(np.sqrt(fit_transform.residuals(
            inlier_points_lr[0], inlier_points_lr[1])**2))
        # Normalize score relative to image size, and flip so range is
        # (0, 1) with 0 being bad, and 1 being good.
        norm_scan_res = np.linalg.norm(np.array(da2.shape))
        norm_score = score / norm_scan_res
    else:  # Fitting failed, set score to max possible (full scan error).
        logger.warning('Drift estimation failed, setting fitting score to '
                       'worst possible.')
        norm_score = WORST_FIT

    logger.debug(f'Drift estimate fitting score (normalized): {norm_score}')

    if display_fit and fit_transform:
        display_estimated_transform(da1, da2, keypoints_lr, fit_transform,
                                    inlier_matches, outlier_matches,
                                    cmap, norm_score, figure)
    return fit_transform, norm_score


def display_estimated_transform(da1: xr.DataArray, da2: xr.DataArray,
                                keypoints_lr: [np.ndarray],
                                mapping: transform.ProjectiveTransform,
                                inlier_matches: np.ndarray,
                                outlier_matches: np.ndarray,
                                cmap: str, fit_score: float,
                                fig: plt.Figure = None):
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
        fit_score: The fitting score.
        fig: matplotlib figure, for displaying. Used if available.
            Defaults to None.
    """
    mosaic = """CAAD
                CBBD"""
    if fig is None:
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
    axd['D'].set_title(f'Image 2 - After Warp\nScore: {fit_score}')

    # Draw translation vector
    pix_trans = mapping.inverse.translation
    mid_pt = np.array([int(da2.shape[1] / 2), int(da2.shape[0] / 2)])

    # In this mode, we feed (x,y) and (u, v), for (x, x+u, y, y+v).
    axd['D'].quiver(mid_pt[0], mid_pt[1], pix_trans[0], pix_trans[1],
                    angles='xy', scale_units='xy', scale=1)
    # NOTE: scale=1 is suggested by docs, but it makes the scale 2x the
    # actual translation.


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
