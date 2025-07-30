"""Script to save drift estimates run offline on two scans."""

import os
import logging
import fire

import numpy as np
import xarray as xr

import matplotlib.pyplot as plt
import matplotlib.font_manager as fm
from mpl_toolkits.axes_grid1.anchored_artists import AnchoredSizeBar

from afspm.components.drift import drift
from afspm.utils import array_converters as ac
import afspm.components.microscope.translators.asylum.translator as asylum

from afspm.utils import log

from skimage import transform
from skimage import exposure
from skimage.measure import ransac


logger = logging.getLogger(log.LOGGER_ROOT + '.scripts.drift.' + __name__)


ASYLUM_EXT = '.ibw'

# Mapping from extension to file loader.
MAP_EXT_FILE_LOADER = {ASYLUM_EXT: asylum.load_scans_from_file}


def find_matching_keypoints(scan1_fname: str, scan2_fname: str,
                            channel_id: str, out_path: str,
                            cmap: str):
    """Perform keypoint matching and outlier estimation and visualize.

    Args:
        scan1_fname: filepath to the first scan.
        scan2_fname: filepath to the second scan.
        channel_id: str id of the channel we are running estimation on.
        out_path: path to save visualized data.
        cmap: str name of colormap to use to visualize the scan data.
    """
    # ----- Setup ----- #
    channel_id = channel_id.upper()

    scan_ext = os.path.splitext(scan1_fname)[1]
    assert scan_ext == os.path.splitext(scan2_fname)[1]

    if scan_ext not in MAP_EXT_FILE_LOADER:
        logger.error(f'No file loader in MAP_EXT_FILE_LOADER for {scan_ext}.')
        return

    load_scans = MAP_EXT_FILE_LOADER[scan_ext]

    both_scans = []
    for fname in [scan1_fname, scan2_fname]:
        scans = load_scans(fname)
        desired_scan = [scan for scan in scans if not channel_id or
                        channel_id in scan.channel.upper()][0]
        both_scans.append(desired_scan)

    da1 = ac.convert_scan_pb2_to_xarray(both_scans[0])
    da2 = ac.convert_scan_pb2_to_xarray(both_scans[1])
    model = drift.create_drift_model()

    # For computation purposes, need to change all NaN to 0
    da1 = da1.fillna(0)
    da2 = da2.fillna(0)

    # Scale intensity
    arrs = [exposure.rescale_intensity(da)
            for da in [da1, da2]]

    # Scale resolution
    scale_factor = 1.0
    arrs = [transform.rescale(arr, scale_factor) for arr in arrs]

    # ----- Keypoints / Feature Vectors ----- #
    try:
        keypoints_lr, descriptors_lr = (
            drift._get_keypoints_and_descriptors_for_list(
                model, arrs, scale_factor))
    except (ValueError, RuntimeError) as e:
        msg = ("Error getting keypoints and descriptors when estimating drift, "
               f"skipping: {e}")
        logger.warning(msg)
        return

    try:
        logger.debug(f'# keypoints L: {keypoints_lr[0].shape[0]}, '
                     f'R: {keypoints_lr[1].shape[0]}')
        logger.debug(f'# descriptors L: {descriptors_lr[0].shape[0]}, '
                     f'R: {descriptors_lr[1].shape[0]}')

        matches, points_lr = drift._match_descriptors(model, keypoints_lr,
                                                      descriptors_lr)
    except (ValueError, RuntimeError) as e:
        msg = ("Error matching descriptors when estimating drift, "
               f"skipping: {e}")
        logger.warning(msg)
        return

    # ----- Transform estimation part ----- #
    match model.fitting:
        case drift.FittingMethod.RANSAC:
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
        case drift.FittingMethod.LEAST_SQUARES:
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
        return

    logger.debug(f'Drift estimate fitting score (normalized): {norm_score}')

    # ----- Drawing / Saving ----- #
    fontprops = fm.FontProperties(size=18)
    rng = np.random.default_rng(seed=0)
    colors = [rng.random(3) for _ in range(len(matches))]

    # Filtered points are our main keypoints
    keypoints0 = points_lr[0]
    keypoints1 = points_lr[1]

    # Scale points / keypoints to be in xarray format!
    scale_factor = np.array(
        [(np.max(da1.y) - np.min(da1.y)).to_numpy() / da1.shape[1],
         (np.max(da1.x) - np.min(da1.x)).to_numpy() / da1.shape[0]])
    keypoints0 *= scale_factor
    keypoints1 *= scale_factor
    keypoints_lr[0] *= scale_factor
    keypoints_lr[1] *= scale_factor

    # --- Individual images --- #
    for da, keypoints, fname in zip(
            [da1, da2],
            [keypoints0, keypoints1],
            [scan1_fname, scan2_fname]):
        fig, ax = plt.subplots(layout='constrained')
        xr.plot.imshow(da, cmap=cmap, add_colorbar=False, add_labels=False,
                       robust=True)

        # NOTE: x- and y- swapped due to how XArray plots...
        ax.scatter(keypoints[:, 0], keypoints[:, 1], c=colors)

        # Add scale bar
        scalebar = AnchoredSizeBar(ax.transData,
                                   2e-6, '2 $\mu$m', 'lower right',
                                   pad=1.0,
                                   color='white',
                                   frameon=False,
                                   fontproperties=fontprops,)

        ax.add_artist(scalebar)

        plt.axis('off')  # Remove axis
        plt.gca().set_aspect('equal')  # Force equal aspect ratio
        plt.savefig(os.path.join(out_path,
                                 os.path.splitext(
                                     os.path.basename(fname))[0]),
                    bbox_inches='tight', transparent=True, pad_inches=0)
        plt.clf()

    # Undo scale factor to return to image coords:
    keypoints0 /= scale_factor
    keypoints1 /= scale_factor
    keypoints_lr[0] /= scale_factor
    keypoints_lr[1] /= scale_factor

    # --- Composite images --- #
    composite_img = np.concatenate([da1.to_numpy(),
                                    da2.to_numpy()], axis=1)  # concat along cols
    for desired_matches, basename in zip(
            [inlier_matches, outlier_matches],
            ['inliers', 'outliers']):
        fig, ax = plt.subplots(layout='constrained')
        # Flipping image along y-axis to match prior plot.
        ax.imshow(np.flip(composite_img, axis=0), cmap=cmap)

        # Save left keypoints
        ax.scatter(keypoints0[:, 0], keypoints0[:, 1], c=colors)
        # Save right keypoints
        ax.scatter(keypoints1[:, 0] + da1.shape[1], keypoints1[:, 1], c=colors)

        # Save lines
        for idx, this_match in enumerate(matches):
            if this_match not in desired_matches:
                continue
            idx0, idx1 = this_match
            # This takes in (x0, x1), (y0, y1).
            # Also, the index of matches is linked to keypoints_lr, *not*
            # the filtered keypoints0/keypoints1.
            ax.plot((keypoints_lr[0][idx0, 1],
                     keypoints_lr[1][idx1, 1] + da1.shape[1]),
                    (keypoints_lr[0][idx0, 0],
                     keypoints_lr[1][idx1, 0]),
                    '-', color=colors[idx])
        plt.axis('off')  # Remove axis
        plt.gca().set_aspect('equal')  # Force equal aspect ratio
        plt.savefig(os.path.join(out_path, basename),
                    bbox_inches='tight', transparent=True, pad_inches=0)
        plt.clf()

    return


def cli_find_matching_keypoints(scan1_fname: str, scan2_fname: str,
                                channel_id: str, out_path: str,
                                cmap: str = 'gray',
                                log_level: str = logging.INFO):
    """Perform keypoint matching and outlier estimation and visualize.

    Args:
        scan1_fname: filepath to the first scan.
        scan2_fname: filepath to the second scan.
        channel_id: str id of the channel we are running estimation on.
        out_path: path to save visualized data.
        cmap: str name of colormap to use to visualize the scan data.
        log_level: level to use for logging.
    """
    log.set_up_logging(log_level=log_level)
    find_matching_keypoints(scan1_fname, scan2_fname, channel_id, out_path,
                            cmap)


if __name__ == '__main__':
    fire.Fire(cli_find_matching_keypoints)
