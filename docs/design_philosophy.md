# Design Philosophy

Scanning Probe Microscopy (SPM) is a complicated business: many different vendors exist, with different subsets of operating modes, differing terminology/units for their various parameters, and different mechanisms to prepare experiments (e.g. how they approach the sample). It would be difficult and time-consuming to try to create an SPM-agnostic library that supports all of these intricacies in a generic manner.

Since the goal is to allow a common 'playground' in which various automation components can live, we choose to limit the knobs and levers we have access to. As such, afspm concerns itself *primarily* with allowing automation of an experiment *after* the SPM has been set up, the tip approached to the sample, and the main operating modes set up. This means that the main scanning mode (e.g. AM-AFM, KPFM) has been set up (with parameters set), and the main spectroscopic mode as well. We hope the majority of experiments can be run in this constrained space, where the automation will only alternate where scans are performed and the type of scan (2d scan vs. spectroscopic).

However, we acknowledge a user may need to have more control during the experiment (e.g., setting different parameters, changing operating modes). To allow this, we are adding some simple accessors: ```REQ_SET_PARAM```, ```REQ_SET_OPERATING_MODE```, and ```REQ_PERFORM_ACTION```. When looking at them, one can clearly see that these accessors are generic/simple: afspm sends a key associated with a mode/parameter, and (potentially) a dictionary of arguments and argument values.

Why do we do this? The smaller percentage of people who need these features can implement them on an as-needed basis, but we do not want to force this into the main afspm package in all cases.

As an example: a research lab may create a child class of GxsmController, XYZLabGxsmController, which implements special operating modes and parameters it needs. This allows two options:
- If the researcher implements this extra functionality in a 'general' way, such that it makes sense to pull it into afspm, we can do so.
- If the researcher chooses to hack something up that works for their experiment (but is not 'general'), they can do so while still taking advantage of afspm.
