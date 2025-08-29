# Writing a Microscope Translator

This document aims to explain the steps necessary to write a new MicroscopeTranslator for an SPM system.

## Overview of MicroscopeTranslator Classes

There are 3 base MicroscopeTranslator classes one can inherit from:
- MicroscopeTranslator
- MapTranslator
- ConfigTranslator

Note that MicroscopeTranslator is the base class from which both MapTranslator and ConfigTranslator inherit. Generally, we recommend you use ConfigTranslator as your base class when implementing a new translator. 

## MicroscopeTranslator

This is the base MicroscopeTranslator, with the main methods that all other translators must implement. It handles the majority of the actual logic linked to responding to requests/publishing state. An SPM-specific controller only needs to focus on the following, in degree of importance:
1. Implementing the abstract ```on_XXX()``` and ```poll_XXX()``` methods.
2. Implementing other parameter set/get support by implementing ```on_param_request()```. Note this is a somewhat exceptional ```on_XXX()``` method.
3. Implementing action support by implementing ```on_action_request()```. Note this is a somewhat exceptional ```on_XXX()``` method.

### Implementing ```on_XXX()```Methods

These methods are 'responses' to specific requests. For example, ```on_start_scan()``` will be called when a microscope translator receives a ```REQ_START_SCAN``` ControlRequest. For each of these, a child controller must:
- Try to perform the action requested.
- Send a ControlResponse indicating the resolution of the request. For example, if you were unable to start a scan because the scanner is not in an ```SS_FREE``` state, you would respond with ```REP_NOT_FREE```.

Note that some of these methods are not *strictly* necessary. For example, ```on_set_zctrl_params()``` can return ```REP_CMD_NOT_SUPPORTED``` if it is not. Check the documentation in afspm/components/microscope/translator.py to see if a particular method *must* be supported or not.

If a method fails, return an appropriate response (e.g. ```$REP_PARAM_ERROR$```). If no specific response exists, either (a) create a new one in control.proto (preferred), or (b) use the generic ```$REP_FAILURE$``` response. Note that we do not expect an ```$on_XXX()$``` method to throw an exception, as we are responding to an explicit request. Because of this, returning that an error occurred (and why) should be fine; the experiment can continue running.

### Implementing ```poll_XXX()``` Methods

These methods are called at a regular cadence by the base MicroscopeTranslator class, in order to determine whether something has changed. A child class *does not* need to hold state or check if something has 'changed'; it merely needs to return what is requested. The rest will be handled by the base controller.

For example, ```poll_scans()``` should return a list of the latest scans received. Note that by 'latest scans', we really mean the latest single- or multi-channel scan, where a Scan2d is a single channel of a scan. 

Note that ```poll_scans()```implies detecting the latest scan and converting it into a list of Scan2d structures. Thus, each SPM controller will need to be able to 'read' its scans and convert them to this SPM-agnostic structure. We expect developers to use pre-existing readers, and adding them as 'optional' packages in the pyproject.toml (within a group defined by the SPM name, e.g. 'gxsm' for a gxsm reader).

These methods are called 'polling' methods, because the base controller regularly 'polls' for them. This is the simplest, most naive method of checking state. 

These methods must return what was requested, but can throw an exception on failure. An exception on failure is desired here, because failing a poll would be an unexpected event (we should always be able to poll for the latest data).

### ```on_param_request()``` Support

This method exists to support 'generic' parameter gets/sets, as defined in afspm/components/microscope/params.py (MicroscopeParameter). While we define a subset of parameters we feel is useful, users can add to the MicroscopeParameter enum as needed.

If a value and units are provided, this request is treated as a 'set' and used to set a given parameter; if not, it is treated as a 'get' and the current value and units are returned.

Note that this method *does not* need to be supported if you are inheriting from the base MicroscopeTranslator. However, if inheriting from ConfigTranslator, this method is necessary for the rest of the logic to run.

### ```on_action_request()``` Support

This method exists to support 'generic' actions requested, as defined in afspm/components/microscope/actions.py (MicroscopeAction). Actions are defined solely by these generic IDs and do not take any parameters.

Note that this method *does not* need to be supported if you are inheriting from the base MicroscopeTranslator. However, if inheriting from ConfigTranslator, this method is necessary for the rest of the logic to run.

## MapTranslator

The MapTranslator class attempts to simplify implementing new translators by using two maps/dicts: a ```param_method_map``` and a ```action_method_map```, each of which maps a generic param/action ID to a method to be used for that given method/action. 

Note that MapTranslator is *not* recommended as a parent class for new MicroscopeTranslators.

## ConfigTranslator

The ConfigTranslator class attempts to simplify implementing new translators by: (a) introducing config files to simplify parameter and action handling; (b) overriding the composite ```on_XXX()``` and ```poll_XXX()``` methods to use ```on_param_request()``` for these; and (c) overriding the 'base' actions to use ```on_action_request()``` for these.

Regarding the config files: 
- In afspm/components/microscope/params.py, the ParameterHandler class reads a TOML-based config file that maps generic parameter IDs to SPM-specific IDs, units, types, and accepted parameter ranges. In doing so, it makes it relatively easy to support many parameters for a given microscope: as long as the ```get_param_spm()``` and ```set_param_spm()``` methods are implemented (abstract methods in ParameterHandler), the handler will use the SPM-specific config file to perform set and get calls.
- In afspm/components/microscope/actions.py, The ActionHandler class reads a TOML-based config file that maps generic action IDs to an appropriate method to call (and parameters to pass) in order to perform said action.

With the appropriate base parameters settable/gettable via ParameterHandler, the class then implements defaults for the ```on_XXX()``` and ```poll_XXX()``` methods which involve composite parameters (such as ScanParameters2d, i.e. ```on_set_scan_params()``` / ```poll_scan_params()```. Similarly, the base actions (e.g. ```REQ_START_SCAN```) are handled automatically using the ActionHandler. 

This greatly simplifies developing a new MicroscopeTranslator. In addition to setting up the ActionHandler and ParameterHandler, a developer only needs to:
- Implement ```poll_scope_state()``` to check the state of the microscope.
- Implement ```poll_scans()```, which should involve using a pre-existing Python package to read the specific SPM's scan save files (and converting them into our generic Scan2d structures).
- Implement ```poll_spec()```, which should involve using a pre-existing Python package to read the specific SPM's spectroscopy save files (and converting them into our generic Spec1d structures).

## Testing

Once a specific MicroscopeTranslator has been implemented, its basic functionality can be tested by using ```tests/components/microscope/translators/test_translator.py```. Note that each SPM may have a special config.toml associated, and/or instructions associated. and review the description of how to run ```test_translator``` in ```test_translator.py```.

## Usage

Similar to above, each SPM may have particulars in terms of how to set it up to run. As such, we expect each SPM implementation to have a short README.md describing this process.
These should be located in the specific SPM's subdirectory (e.g. afspm/components/microscope/translators/gxsm for the GxsmTranslator).
