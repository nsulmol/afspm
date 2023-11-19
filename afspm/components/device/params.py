"""Holds a mapping of get/set params and descriptions.

This is particularly the param id *as we would call it from within afspm*.
The purpose is to abstract away setting specifics, so that we can use the
same param id for multiple different device controllers.

In this file, we have a map of param_ids with descriptions. Each device
controller will have their own params map, mapping a param_id to a method
to receive it.
"""

from enum import Enum


class DeviceParameter(str, Enum):
    OPERATING_MODE = 'operating-mode'

    ZCTRL_FB_ENABLED = 'zctrl-fb-enabled'
    ZCTRL_FB_SETPOINT = 'zctrl-fb-setpoint'
    ZCTRL_FB_PGAIN = 'zctrl-fb-pgain'
    ZCTRL_FB_IGAIN = 'zctrl-fb-igain'
    ZCTRL_FB_ERRORGAIN = 'zctrl-fb-errorgain'

    TIP_BIAS_VOLTAGE = 'tip-bias-voltage'
    TIP_VIBRATING_AMPL = 'tip-vibrating-ampl'
    TIP_VIBRATING_FREQ = 'tip-vibrating-freq'

    AMPL_FB_ENABLED = 'ampl-fb-enabled'
    AMPL_FB_SETPOINT = 'ampl-fb-setpoint'
    AMPL_FB_PGAIN = 'ampl-fb-pgain'
    AMPL_FB_IGAIN = 'ampl-fb-igain'

    PLL_FB_ENABLED = 'freq-fb-enabled'
    PLL_FB_SETPOINT = 'freq-fb-setpoint'
    PLL_FB_PGAIN = 'freq-fb-pgain'
    PLL_FB_IGAIN = 'freq-fb-igain'


DESCRIPTIONS = {
    DeviceParameter.OPERATING_MODE:
    "The current system operating mode (e.g. FM-AFM). str expected.",

    DeviceParameter.ZCTRL_FB_ENABLED:
    "Whether or not the z-ctrl feedback system is on.",
    DeviceParameter.ZCTRL_FB_SETPOINT:
    "Set point of z-controller feedback. Unit dependent on mode.",
    DeviceParameter.ZCTRL_FB_PGAIN:
    "Gain of the proportional component of PI system. Units in X.",
    DeviceParameter.ZCTRL_FB_IGAIN:
    "Gain of the integral component of PI system. Units in X.",
    DeviceParameter.ZCTRL_FB_ERRORGAIN:
    "Gain applied to error b/w input signal and setpoint, before feeding to"
    "PI controller. Units in X.",

    DeviceParameter.TIP_BIAS_VOLTAGE:
    "Bias voltage applied to the tip. Unit is V.",
    DeviceParameter.TIP_VIBRATING_AMPL:
    "Free amplitude of the cantilever. Units in V.",
    "tip-vibrating-freq": "Free frequency of the cantilever. Units in Hz.",

    # Amplitude Feedback
    DeviceParameter.AMPL_FB_ENABLED:
    "Whether or not the amplitude feedback is on.",
    DeviceParameter.AMPL_FB_SETPOINT:
    "Amplitude setpoint, in % off of free amplitude.",
    DeviceParameter.AMPL_FB_PGAIN:
    "Gain of the proportional component. Units in X.",
    DeviceParameter.AMPL_FB_IGAIN:
    "Gain of the integral component. Units in X.",

    # Frequency Modulation
    DeviceParameter.PLL_FB_ENABLED:
    "Whether or not the frequency feedback is on.",
    DeviceParameter.PLL_FB_SETPOINT:
    "Frequency setpoint, in % off of free amplitude.",
    DeviceParameter.PLL_FB_PGAIN:
    "Gain of the proportional component. Units in X.",
    DeviceParameter.PLL_FB_IGAIN:
    "Gain of the integral component. Units in X.",
}
