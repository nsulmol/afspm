# Choosing a Scheduler

There are two default schedulers in afspm: the base MicroscopeScheduler, and the DriftCompensatedScheduler. The former performs the basic functions of (a) scheduling control of the microscope, and (b) caching data to send to new components. The latter augments this with logic to track drift over the duration of an experiment. It allows components to think only in the drift-corrected Sample Coordinate System (SCS), while the microscope remains in the drift-varying Tip Coordinate System (TCS).

We recommend using the DriftCompensatedScheduler if your experiment spans a sufficient time period that drift compensation is necessary. Please note that you will need to look into the drift/scheduler.py file to understand the various parameters that must be set.

