"""Drifting ImageTranslator"""
import logging
import time
import numpy as np

from afspm.utils.log import LOGGER_ROOT
from afspm.utils.units import convert
from afspm.components.microscope.translators.image import translator

logger = logging.getLogger(LOGGER_ROOT + '.samples.point_subscan_drift.' + __name__)


class DriftImageTranslator(translator.ImageTranslator):
    """Variant of ImageTranslator that drifts.

    Attributes:
        drift_rate: (float, float) containing the drift rate in x and y
            dimensions.
        spatial_units: str indicating the spatial units of our drift rate.
        temporal_units: str indicating the temporal units of our drift rate.
        last_scan_ts: timestamp of last scan, used to estimate drift
            over time.
    """

    def __init__(self, drift_rate: tuple[float, float],
                 spatial_units: str, temporal_units: str, **kwargs):
        """Init translator."""
        self.drift_rate = np.array(drift_rate)
        self.spatial_units = spatial_units
        self.temporal_units = temporal_units
        self.last_scan_ts = time.time()
        super().__init__(**kwargs)

    def update_scan(self):
        """Override, to update drift."""
        self.update_drift()
        super().update_scan()

    def update_drift(self):
        # TODO: Consider using datetime and timedelta instead?
        new_ts = time.time()
        time_delta_s = new_ts - self.last_scan_ts
        self.last_scan_ts = new_ts

        time_delta = convert(time_delta_s, 's', self.temporal_units)

        drift_vec = self.drift_rate * time_delta

        drift_vec[0] = convert(drift_vec[0],
                               self.spatial_units,
                               self.dev_img.x.attrs['units'])
        drift_vec[1] = convert(drift_vec[1],
                               self.spatial_units,
                               self.dev_img.y.attrs['units'])

        x2 = self.dev_img.x + drift_vec[0]
        y2 = self.dev_img.y + drift_vec[1]
        length_units = self.dev_img.x.attrs['units']
        self.dev_img = self.dev_img.assign_coords(x=x2, y=y2)
        self.dev_img.x.attrs['units'] = length_units
        self.dev_img.y.attrs['units'] = length_units
