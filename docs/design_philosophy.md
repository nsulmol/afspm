# Design Philosophy

Scanning Probe Microscopy (SPM) is a complicated business: many different vendors exist, with different subsets of operating modes, differing terminology/units for their various parameters, and different mechanisms to prepare experiments (e.g. how they approach the sample). It would be difficult and time-consuming to try to create an SPM-agnostic library that supports all of these intricacies in a generic manner.

Since the goal is to allow a common 'playground' in which various automation components can live, we choose to limit the knobs and levers we control to those all SPM systems will support. As such, afspm concerns itself *primarily* with allowing automation of an experiment *after* the SPM has been set up, the tip approached to the sample, and the main operating modes set up. This means that the main scanning mode (e.g. AM-AFM, KPFM) has been set up (with parameters set), and the main spectroscopic mode as well. We hope the majority of experiments can be run in this constrained space, where the automation will only alternate where scans are performed and the type of data collected (2d scan vs. spectroscopic).

At it's base level, all requests to the microscope can be decoupled into two types of requests: parameter requests, where one requests to get or set a particular parameter associated with the microscope (such as setting the scan size); and action requests, where one requests that the microscope perform a particular action (such as starting a scan). Most other requests are composites of these (such as a composite request to set scan parameters, which includes multiple individual parameters associated with a 2D scan). Any new parameters one desired could be added and set via the parameter setting methods. Similarly, any new actions one desired could be added and used via the action setting methods.

This division can be considered for future expansion beyond simply adding new parameters or actions.
If one wished to add the ability to change operating modes (e.g. switching between AM-AFM and FM-AFM), one could define an action for each. This would update the mode without providing any parameters associated with that mode. Alternatively, one could add a composite parameter request that included the parameters associated with a given mode.

Why do we do limit our 'base' controls so? The smaller percentage of people who need extra features can implement them on an as-needed basis, but we do not want to force this into the main afspm package in all cases. Over time, new parameters/actions that are requested by the community may be added to the 'base set of parameters and actions.

As an example: a research lab may create a child class of GxsmController, XYZLabGxsmController, which implements special operating modes and parameters it needs. This allows two options:
- If the researcher implements this extra functionality in a 'general' way, such that it makes sense to pull it into afspm, we can do so.
- If the researcher chooses to hack something up that works for their experiment (but is not 'general'), they can do so while still taking advantage of afspm.

## On Units and Unit Conversions

Different device controllers use different units for their equivalent attributes. This can be fairly confusing, and may introduce issues. Rather than standardize units everywhere, we impose a policy: all data is sent with units, and it is the *receiver's* responsibility to convert the data to the units they desire.

To make this easier, we take advantage of a handy python package called pint. In utils/units.py, there are some conversion methods to help using this. Essentially, units are defined as a str, with most units you would deal with existing in the pint registry. If, for some reason, your SPM has an unusual unit and unit conversion, you should modify the registry in units.ureg (on startup).
