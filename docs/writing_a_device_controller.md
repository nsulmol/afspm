# Writing a Device Controller

This document aims to explain the steps necessary to write a new DeviceController for an SPM system.

## Division of Labor

As a reminder, let us differentiate what each of the 'main' components in an experiment do (and what afspm is responsible for, versus each DeviceController implementation).

### AfspmController

The AfspmController mediates control between a DeviceController and all other components of the experiment (only one component is in control at a time). Additionally, it:
- Maintains a cache of messages sent by the DeviceController, to resend to any late-connecting or crashed-and-restarted components. If the cache is defined well, each component should have enough information to hit-the-ground running on spawn.
- Maintains the current 'problems set' of the experiment. Essentially, any component (in-control or not) can flag or unflag a problem in the experiment. This is the main mechanism by which different automation components might take control. For example, a TipAnalyzer may flag that the tip state is improper, which will cause (a) the current component to lose control, and (b) a component that is designed to *fix* that problem to connect.

### DeviceController (Abstract)

There is a base DeviceController, which all SPM-specific controller can inherit from. It handles the majority of the actual logic linked to responding to requests/publishing state. An SPM-specific controller only needs to focus on the following, in degree of importance:
1. Implementing the abstract ```on_XXX()``` and ```poll_XXX()``` methods.
2. Implement other parameter set/get support by adding to ```self.param_method_map```.
3. Support different operating modes (NOT YET AVAILABLE).
4. Support 'actions' (e.g. approaching the tip). (NOT YET AVAILABLE).

To have a functional device controller for most experiments, you only need to implement (1).

## A Minimal Controller

### Implementing ```on_XXX()```Methods

These methods are 'responses' to specific requests. For example, ```on_start_scan()``` will be called when a device controller receives a ```REQ_START_SCAN``` ControlRequest. For each of these, a child controller must:
- Try to perform the action requested.
- Send a ControlResponse indicating the resolution of the request. For example, if you were unable to start a scan because the scanner is not in an SS_FREE state, you would respond with REP_NOT_FREE.

Note that some of these methods are not *strictly* necessary. For example, ```on_set_zctrl_params()``` can return ```REP_CMD_NOT_SUPPORTED``` if it is not. Check the documentation in afspm/components/device/controller.py to see if a particular method *must* be supported or not.

If a method fails, return an appropriate response (e.g. $REP_PARAM_ERROR$). If no specific response exists, either (a) create a new one in control.proto (preferred), or (b) use the generic $REP_FAILURE$ response. Note that we do not expect an $on_XXX()$ method to throw an exception, as we are responding to an explicit request. Because of this, returning that an error occurred (and why) should be fine; the experiment can continue running.

### Implementing ```poll_XXX()``` Methods

These methods are called at a regular cadence by the base DeviceController class, in order to determine whether somethinig has changed. A child class *does not* need to hold state or check if something has 'changed'; it merely needs to return what is requested. The rest will be handled by the base controller.

For example, ```poll_scans()``` should return a list of the latest scans received. Note that by 'latest scans', we really mean the latest single- or multi-channel scan, where a Scan2d is a single channel of a scan. 

Note that ```poll_scans()```implies detecting the latest scan and converting it into a list of Scan2d structures. Thus, each SPM controller will need to be able to 'read' its scans and convert them to this SPM-agnostic structure. We expect developers to use pre-existing readers, and adding them as 'optional' packages in the pyproject.toml (within a group defined by the SPM name, e.g. 'gxsm' for a gxsm reader).

These methods are called 'polling' methods, because the base controller regularly 'polls' for them. This is the simplest, most naive method of checking state. We purposefully chose this, to try to make the job of implementing a new DeviceController easier.

These methods must return what was requested, but can throw an exception on failure. An exception on failure is desired here, because failing a poll would be an unexpected event (we should always be able to poll for the latest data).

### Missing Support

We should note an elephant-in-the-room in terms of missing support: spectroscopic data. Today, afspm does not have the structures/logic implemented to run spectroscopic scans, a very common and essential aspect of many SPM experiments. We plan to implement support for it in the future.

## Additional Parameter Setting

The above methods define a basic controller. However, an experiment may desire to change some other SPM-specific parameter that is not defined by these. We should note that this 'addditional' parameter support *should* be incredibly rare! We want to minimize the usage of these kinds of calls, as they impose exponential testing and support requirements on every other controller! Because of this, we suggest using additional parameters sparingly.

To support extra parameters, we have added a REQ_PARAM request; in the base DeviceController class, it is mapped to the method $_handle_param_request$, which looks for the appropriate method to call for a given parameter by checking for a 'parameter' key in a $ParamMethodMap$ mapping, that maps from string keys to method values. Thus, a DeviceController may implement support for a parameter $PARAM_A$ by adding a key:val pair $PARAM_A$:$SetGetParamA$ to $ParamMethodMap$. If such a key does not exist, $_handle_param_request$ will return a response $REP_PARAM_NOT_SUPPORTED$, indicating that the controller does not support this parameter.

We should note that there is 1 parameter that we *do* suggest implementing, for testing purposes: scan speed. The test_controller.py tests will optionally set the physical scan size, data points, and scan speed in order to speed up the tests.

## Operating Modes and Actions (NOT YET AVAILABLE)

Aside from setting scan parameters and scanning, one *may* feel the need to change the configuration of their SPM *during* an experiment. As described in design_philosophy.md, afspm primarily assumes that the user has performed the following *before* starting their afspm experiment:
1. Prepared their sample, including any process of setting up vacuum or temperature conditions.
2. Configured their scanning and spectroscopic modes. Thus, we assume a user will not change the 'mode' they are scanning (e.g. AM-AFM), nor the type of spectroscopic scans they are making (e.g. i-V curves).
3. Approached the sample to the surface.

However, we acknowledge that these are somewhat limiting constraints. Because of this, we plan to implement the following in the future (in order of priority):
1. Operating Modes Support.
2. Actions Support.

### Operating Modes Support

The idea here is to allow afspm to send a ```REQ_SET_OPERATING_MODE``` call, with a string 'key' associated to the mode, and an optional dictionary of key:val pairs for 'setting up' the mode. For example, one might call operating mode key 'AM-AFM', with arguments defining parameters for this mode.

The base controller will do *no more* than pass this information to the child (SPM-specific) controller. We do this consciously for a couple of reasons:
- Implementing a lot of this logic in the base controller increases its complexity and the expectations of a child DeviceController. We do not want to force a user to implement the 'AM-AFM' mode if they have no need for it! 
- Particular SPM devices may require different parameters or steps to be taking to 'set' into an operating mode. By passing the dict as-is, we allow each SPM implementation to deal with their own set of arguments.
- Tied to the above: this removes the requirement for a universal translator for all SPM parameters! If we explicited, for example, that AM-AFM mode requires setting the oscillating signal feedback loop's PID values, it means that every SPM controller will need to have a mapping for ```oscillating_proportional_gain```, etc.

While this degree of complexity may *evolve* naturally, as more users use and extend afspm, there is *no guarantee* that this will happen. As such, we want to have a minimally defined way of using this code.

### Actions Support

An action here is defined as a set of steps that an SPM might take, associated to a particular string 'key'. For example, ```APPROACH_TIP``` could correspond to the action of approaching the tip. This is the way that, for example, gxsm has implemented such logic. In doing so, we again decouple action specifics on an SPM-by-SPM basis. The same logic above applies.

## Testing

Once a specific DeviceController has been implemented, its basic functionality can be tested by using tests/components/controller/test_controller.py. Note that each SPM may have a special config.toml associated, and/or instructions associated.

## Usage

Similar to above, each SPM may have particulars in terms of how to set it up to run. As such, we expect each SPM implementation to have a short README.md describing this process.
