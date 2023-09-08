"""Base visualizer component to display scans from cache."""

import logging
from typing import Callable
from enum import Enum
import matplotlib.pyplot as plt
import numpy as np
import xarray as xr

from google.protobuf.message import Message

from .afspm_component import AfspmComponent
from ..io.protos.generated import scan_pb2
from ..utils import array_converters as ac

logger = logging.getLogger(__name__)


class CacheMeaning(Enum):
    """Holds cache meaning for visualization purposes."""
    TEMPORAL = 1  # Only display the latest scan
    REGIONS = 2  # Treat all scans in cache key as regions.


class VisualizationStyle(Enum):
    """Holds visualization style."""
    COLORMESH = 1  # xarray.plot.pcolormesh()
    IMSHOW = 2  # xarray.plot.imshow()
    CONTOUR = 3  # xarray.plot.countour()
    CONTOUR_FILLED = 4  # xarray.plot.countourf()
    SURFACE = 5  # xarray.plot.surface()


# TODO: Write unit test for this.
class Visualizer(AfspmComponent):
    """Base visualizer, to display scans from cache.

    This base component allows easy visualization of 2D data from the
    subscriber cache, using matplotlib.pyplot and xarray's internal matplotlib
    integration.

    A user provides, for each Scan2d cache key of interest:
    - The cache_meaning, either 'temporal' or 'regions' (defaulted to
    'temporal').
    - The scan_extents of the visualization, in case the scan should be drawn
    as a sub-ROI of a larger extent.
    - The visualization style to use when drawing; these are options available
    from xarray's plotting features (which come from matplotlib).
    - The visualization colormap, if a special one is desired. This is fed
    directly to matplotlib, so make sure you use a real one.

    For any Scan2d cache keys not declared but received by the subscriber (due
    to registration), visualize_undeclared_scans determines whether they are
    visualized. In such a case, the default visualization logic (temporal) is
    used.

    Upon setup, visualizer will create a new figure for each scan2d key in
    the cache. These will be updated every time a new scan2d is received, and
    the matplotlib backend is updated within the run_per_loop() method of
    AfspmComponent. Note that we are recreating all images and figures every
    time a new scan is provided (*quite inefficient*), as opposed to
    determining which cache key was updated and only updating that.

    Attributes:
        cache_meaning_map: dictionary containing scan_envelope:cache_meaning
            pairs. Used to determine if a cache's data is considered to be
            'temporal' (meaning we only show the latest image); or 'regions',
            meaning we treat all as being sub-regions of a larger region.
        scan_phys_extents_map: dictionary containing scan_envelope:phys_extents
            pairs. For a scan_envelope, None (or no key) means we visualize the
            data as-is; having extents means we visualize it as being within
            the provided extents. Useful to illustrate the scan within the full
            scan region, for example. Note that extents are *required* for
            'regions' visualizations.
        scan_data_extents_map: dictionary containing scan_envelope:data_extents
            pairs. For a scan_envelope, None (or no key) means we visualize the
            data as-is; having extents means we visualize it as being within
            the provided extents. Useful to illustrate the scan within the full
            scan region, for example. Note that extents are *required* for
            'regions' visualizations.
        visualization_style_map: dictionary containing scan_envelope:viz_style
            pairs. These map to xarray plotting styles.
        visualization_colormap_map: dictionary containing scan_envelope_
            colormap pairs. These are fed to the xarray/matplotlib plotting
            cmap attribute.
        visualize_undeclared_scans: bool, determines whether undeclared scan
            envelopes are visualized or not. If so, we visualize them with
            'temporal' cache meaning and no scan extents.
        scan_id: stores the cache base id for any Scan2d.

        plt_figures_map: dictionary containing scan_envelope:pyplot_figure
            pairs. Part of matplotlib backend, used for visualization.
    """
    def __init__(self, cache_meaning_map: dict[str, str],
                 scan_phys_extents_map: dict[str, tuple[float, float]],
                 scan_data_extents_map: dict[str, tuple[int, int]],
                 visualization_style_map: dict[str, str],
                 visualization_colormap_map: dict[str, str],
                 visualize_undeclared_scans: bool,
                 scan_id: str,
                 **kwargs):
        # Validate all maps have same keys
        for viz_map in [scan_phys_extents_map, scan_data_extents_map,
                        visualization_style_map, visualization_colormap_map]:
            assert cache_meaning_map.keys() == viz_map.keys()

        self.cache_meaning_map = cache_meaning_map
        self.scan_phys_extents_map = scan_phys_extents_map
        self.scan_data_extents_map = scan_data_extents_map
        self.visualization_style_map = visualization_style_map
        self.visualization_colormap_map = visualization_colormap_map

        self.visualize_undeclared_scans = visualize_undeclared_scans
        self.scan_id = scan_id

        self.plt_figures_map = {}
        self._set_up_visualization()
        super().__init__(**kwargs)

    def _set_up_visualization(self):
        """Initializes plt and figures for each cache key provided."""
        for key in self.cache_meaning_map:
            if (self.cache_meaning_map[key].upper() ==
                    CacheMeaning.REGIONS.name and
                    (self.scan_phys_extents_map[key] is None or
                     self.scan_data_extents_map[key] is None)):
                msg = ("Scan data with key %s is of meaning REGIONS "
                       "with no extents. Not currently supported!",
                       key)
                logger.error(msg)
                raise KeyError(msg)

            self.plt_figures_map[key] = plt.figure()
        plt.show(block=False)

    def run_per_loop(self):
        """Override to update figures every loop."""
        for __, fig in self.plt_figures_map.items():
            fig.canvas.draw_idle()
            fig.canvas.flush_events()

    def on_message_received(self, envelope: str, proto: Message):
        """Override; we update the visualization data on new scans."""
        if isinstance(proto, scan_pb2.Scan2d):
            self.update_visualization_data()

    def update_visualization_data(self):
        """For every cache key, updates visualization data."""
        if self.visualize_undeclared_scans:
            keys = [key for key in self.subscriber.cache if self.scan_id in key]
        else:
            keys = list(self.cache_meaning_map)

        for key in keys:
            if key not in self.cache_meaning_map:
                self._add_to_visualizations(key)

            if (self.cache_meaning_map[key].upper() ==
                    CacheMeaning.TEMPORAL.name):
                scan_xarr = ac.convert_scan_pb2_to_xarray(
                    self.subscriber.cache[key][-1])  # Last value in hist
            elif (self.cache_meaning_map[key].upper() ==
                    CacheMeaning.REGIONS.name):
                scan_xarr = self._create_regions_xarray(key)
            else:
                logger.error("Visualization requested with unsupported cache "
                             "meaning. Not displaying.")
                continue

            # Reset prior plot
            self.plt_figures_map[key].clear()

            # Plot
            cmap = self.visualization_colormap_map[key]
            viz_style = self.visualization_style_map[key].upper()

            if viz_style == VisualizationStyle.SURFACE.name:
                axes = self.plt_figures_map[key].add_subplot(projection='3d')
            else:
                axes = self.plt_figures_map[key].add_subplot()

            viz_method = None
            if viz_style == VisualizationStyle.COLORMESH.name:
                viz_method = scan_xarr.plot.pcolormesh
            elif viz_style == VisualizationStyle.IMSHOW.name:
                viz_method = scan_xarr.plot.imshow
            elif viz_style == VisualizationStyle.CONTOUR.name:
                viz_method = scan_xarr.plot.contour
            elif viz_style == VisualizationStyle.CONTOUR_FILLED.name:
                viz_method = scan_xarr.plot.contourf
            elif viz_style == VisualizationStyle.SURFACE.name:
                viz_method = scan_xarr.plot.surface
            else:
                logger.error("Visualization requested with unsupported style "
                             "%s. Not displaying", viz_style)
                continue

            viz_method(ax=axes, cmap=cmap)

    def _create_regions_xarray(self, key: str) -> xr.DataArray:
        """Creates a 'regions' xarray, for visualization.

        A 'regions' image is an image where we merge all cached scans from a
        key, treating them as ROIs in a larger image. The scan_phys_extents
        define the overall size of the image, and the phys_data_extents the
        image resolution.

        NOTE: we probably do not need phys_data_extents, if we used xarray's
        'merge' method...
        """
        # TODO: Try to reimplement using xarray's merge.
        cache_list = self.subscriber.cache[key]
        scan_phys_extents = self.scan_phys_extents_map[key]
        scan_data_extents = self.scan_data_extents_map[key]
        data_units = cache_list[0].params.data.units
        phys_units = cache_list[0].params.spatial.units

        x = np.linspace(0, scan_phys_extents[0], scan_data_extents[0])
        y = np.linspace(0, scan_phys_extents[1], scan_data_extents[1])
        xarr = xr.DataArray(dims=['y', 'x'],
                            coords={'y': y, 'x': x},
                            attrs={'units': data_units})
        xarr.x.attrs['units'] = phys_units
        xarr.y.attrs['units'] = phys_units

        for scan in cache_list:
            origin = np.array([scan.params.spatial.roi.top_left.x,
                               scan.params.spatial.roi.top_left.y])
            size = np.array([scan.params.spatial.roi.size.x,
                             scan.params.spatial.roi.size.y])

            data = np.array(scan.values, dtype=np.float64)
            data = data.reshape((scan.params.data.shape.x,
                                 scan.params.data.shape.y))

            xarr.loc[{'x': slice(origin[0], origin[0] + size[0]),
                      'y': slice(origin[1], origin[1] + size[1])}] = data
        return xarr

    def _add_to_plt_maps(self, key: str):
        self.plt_figures_map[key] = plt.figure()

    def _add_to_visualizations(self, key: str):
        """Add a new key to our visualization maps."""
        self.cache_meaning_map[key] = CacheMeaning.TEMPORAL.name
        self.scan_phys_extents_map[key] = None
        self.scan_data_extents_map[key] = None

        self._add_to_plt_maps(key)
