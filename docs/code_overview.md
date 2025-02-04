# Code Overview

# Package Structure
The afspm package itself is divided as follows:
- ```components```: contains the pre-existing AfspmComponents. These are the components that will run in your experiment, the top-level logic.
- ```io```: contains the communications particulars, for sending data between components. Unless you are particularly curious about the zmq internals, you only need to worry yourself with the data structures defines in the protos subdirectory.
- ```utils```: general utilities methods. Note that parser.py exists here, which is useful to understand at a high-level (but not worth delving into unless you are working on it).

We expand a bit upon the purpose of each (and their subdirectories) below.

## Components
The main 'base' components are:
- ```AfspmComponent```: all components inherit from this!
- ```AfspmComponentMonitor```: this monitor 'spawns' each component and monitors them. If a component has died by mistake, it will respawn it; if on-purpose it will accept this sad fact. spawn.py passes all components to be spawned (and their arguments) to the monitor.
- ```MicroscopeScheduler```: the 'mediator' class, to determine who has control of the SPM at an instance in time.
- ```MicroscopeTranslator```: the base translator class, for communicating directly with the SPM.

SPM-specific translators are defined in subdirectories within components/microscope/translators. The other components in here may be of use for particular experiments.

## I/O

Communication between components is divided into:
- ```control```: control requests and responses that are sent to the MicroscopeScheduler (and forwarded to the MicroscopeTranslator, if appropriate). This is for explicitly controlling the SPM and flagging/unflagging 'problems' that have been detected.
- ```heartbeat```: all components open heartbeat sockets, to indicate whether they are alive. AfspmComponentsMonitor uses these to determine if a component is still alive or needs to be respawned.
- ```pubsub```: this publisher/subscriber logic is how the SPM MicroscopeTranslator informs the rest of the components on what is happening. State changes, scans, etc are published by the translator, and all components can subscribe to listen to what they are interested in.
- ```protos```: this holds the protobuffer files, i.e. the structure that are sent between components (over zeromq).

### Protobuffer Files

This is worth highlighting, as everyone who uses afspm will need to understand its basics. The data structures that are passed around in afspm are all pre-defined protobuffer files. Thus, for example, a specific channel scan is a Scan2d message, as seen in protos/scan.proto. In order to send data between components, they need to be defined in a proto.

In the top-level readme, you will see that these protos must be *compiled* into language-specific files. This is critical, as they are used to encode and decode our data into actual structures!

Of note, the main protobuffer data structures are found in:
- ```control.proto```: this defines control requests/responses, control modes, experiment problems, and the structures by which these are all sent (e.g. the ControlState). These protos are sent/received via the 'control' i/o path.
- ```geometry.proto```: these are base structures for dealing with geometric data, such as points and rectangles associated with scans. They are used by other protos.
- ```scan.proto```: these are the main messages/structures sent out by the MicroscopeTranslator (i.e., sent out via the 'pubsub' i/o path). The 2d scans are defined here, as is the scope state, and parameters associated with a given scan.
- ```analysis.proto```: these are some pre-defined structures for sending analysis results between components.

## Utils

This contains a number of helper scripts. Of note are:
- array_converters.py
- parser.py

### Array Converters

This file contains methods for converting between common scan data structures and the generic Scan2d structure. It may be added to if other common data structures are used.

### Parser

This file contains the main logic by which we 'spawn' components. All the components that need to be spawned in an experiment are defined in a single TOML config file. This file should also contain all arguments and arg values to spawn them. The parser is responsible for taking this TOML structure and parsing it into a list of component dicts to be created (via AfspmComponentsMonitor). Please see example config files in the samples directory, for reference.


# Tests and Samples

In addition to the main package, a number of tests have been defined, in the tests subdirectory. Additionally, a number of sample experiments have been defined in the samples directory. These should serve as guidance/pointers to implementing your own experiments. Note that some/most of these samples use an 'ImageTranslator', which allows faking scans over a provided image. This is useful for testing/working through the logic of your experiment.
