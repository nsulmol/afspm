# Design Philosophy

Scanning Probe Microscopy (SPM) is a very complicated business! As such, many different vendors with different specialities exist. Different SPM systems support different subsets of operating modes, with differing terminology/units for their various parameters, and different mechanisms to prepare experiments (e.g. how they approach the sample). As such, it would be *very* difficult and time-consuming to try to create an SPM-agnostic library that supports all of these intricacies!

As such, we are trying to develop afspm with a very explicit *scope*. Since the goal is to allow a common 'playground' in which various automation components can live, we choose to limit the knobs and levers they have. Without doing this, the complexity of this library would simply be too high to realistically support.

Thus: afspm concerns itself *primarily* with allowing automation of an experiment *after* the SPM has been set up, the tip approached to the sample, and the main operating modes set up. This means that the main scanning mode (e.g. AM-AFM, KPFM) has been set up (with parameters set), and the main spectroscopic mode as well. We hope the majority of experiments can be run in this constrainted space, where the automation will only alternate where scans are performed and the type of scan (2d scan vs. spectroscopic).

However, we acknowledge that this is unrealistic. It is possible a user will need to set some specific parameters while running an experiment. It is also possible an experiment will require *changing* operating modes. To allow this, we are adding some simple accessors to do so (search for ```REQ_SET_PARAM```, ```REQ_SET_OPERATING_MODE```, and ```REQ_PERFORM_ACTION``` ). When looking at them, one can clearly see that these accessors are very generic/simple: afspm sends a key associated with a mode/parameter, and optionally a dictionary of arguments and argument values.

Why do we do this? Again, we *need* to constrain the complexity of this system if we expect it to not break in the future. The smaller percentage of people who need these features can implement them on an as-needed basis, but we do not want to force this into the main afspm package.

More concretely: a research lab may create a child class of GxsmController, XYZLabGxsmController, which implements special operating modes and parameters it needs. If/when they share this code, they share the controller so others can use it as necessary.

This allows two extra things:
- If the researcher implements this extra functionality in a 'general' way, such that it makes sense to pull it into afspm, we can do so.
- If the researcher chooses to hack something up that works for their experiment (but is not 'general'), they can do so while still taking advantage of afspm.

### A Caveat

Since we are trying to develop useful automation components, certain new functionality *will* be included to afspm, with associated protobuffer structures. For example, we have added a ZCtrlParameters structure and methods to change the current z control feedback. We will implement this (as much as possible) as optional structures and associated methods. However, we hope these will be useful to users!
