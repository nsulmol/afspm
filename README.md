# afspm

An Automation Framework for Scanning Probe Microscopy.

## Overview

In the world of Scanning Probe Microscopy (SPM), many different vendors exist with different file formats and scripting languages. Partially due to this, most SPM experiments are designed as one-offs, with scripts written for one particular system; in short, they are not designed to be shared.

The value of sharing these scripts has increased significantly recently, as recent research has shown high potential in automating multiple aspects of these experiments. 

To aid the situation, we have developed a system-agnostic protocol for communicating between 'components' in a 'network', including communicating with an SPM controller. By developing automation components in this system-agnostic manner, we allow them to be reused by other researchers on their differing SPM systems - all that is needed is a single 'Device Controller' that communicates with the SPM system of interest. 

Put differently:
- Code can be written in this system-agnostic abstraction.
- Information is translated into a specific SPM's language and API only at one point: the device controller. Thus, a new SPM system can be made compatible with afspm simply by writing a DeviceController for it.
- If a researcher uses an SPM system that is already supported by afspm (i.e., a DeviceController for it exists), they can run any of the existing automation components 'for free'.

We aim to develop a number of 'key' automation components within this framework, and hope to incentivize other researchers to develop on top of it.

Note: for more information on the overall design (and justification), look in the docs folder.

## Requirements

afspm relies on the following libraries:
- numpy, for data manipulation.
- pyzmq, as the abstraction layer allowing different communication protocols.
- protobuf, for encoding/decoding the data we send over our protocol.
- tomli, to read our TOML-defined configuration files.
- fire, for argument parsing/handling when invoked from a terminal.
- pint, for unit conversions.

Additionally, we have the following 'hard' requirements (although they are only used by some components):

- xarray, as a general labeled data format we can convert to/from.
- imageio, to read images for tests.
- pysimplegui, for some simple UIs.
- scipy, for some data manipulation.
- matplotlib, for visualizing some data (using xarray data structures).

## Installation

This project is developed using poetry. To install, you should clone the repository and call poetry install:

``` sh
git clone https://github.com/nsulmol/afspm afspm  # Put in afspm subdirectory
cd afspm
poetry install
```

Next, you should:
1. Compile and prepare protobuffer files.
2. Validate the installation (with pytest).

### Set up and compile protobuffer files

We use Google protobuffers to serialize/deserialize data between the various system components. We chose it over other options (e.g. JSON, YML/YAML) because:
- *It has multi-language/multi-platform support*: unlike pickle (default Python option), a compiled protobuf message can be sent/received by many languages, on any of the 3 main platforms (Windows, Linux, Mac).
- *It guarantees type-safety and avoids schema-violations*: we can be certain a message prepared does not break our schema, so we avoid unnecessary bugs / exception handling.

Now, there is one aspect that could be construed as either a pro or con: *protobuf messages are not human readable*. While human readability is, in principle, a huge plus, it tends to go hand-in-hand with a lack of type safety/easy schema violations. What we mean by this: a user can easily unintentionally send a broken message when they are able to create it with a simple text editor. Thus, we will *accept* this 'con' given our perceived larger 'pro'.

#### Set up Google Protobuf Compiler
Download the protobuf compiler. The easiest way is to download the latest precompiled binaries from their releases: https://github.com/protocolbuffers/protobuf/releases. Note that you will need to grab the *appropriate* package for your operating system. E.g., ```protoc-25.2-win64.zip```for a 64-bit Windows environment, or ```protoc-25.2-linux-x86_64.zip```for a 64-bit x86-based Linux environment (where 25.2 was the latest stable build in this case).

Once downloaded, you will need to copy the executable and included well-known types to appropriate locations (so they are automatically detected).

On Linux/OS X:
1. Copy/move the files in ./bin to /usr/local/bin.
2. Copy/move the files in ./include to /usr/local/include.

On Windows (where ```$DIR``` is your chosen directory):
1. Copy/move the ./bin folder to ```$DIR```and add ```$DIR/bin```to your ```PATH```.
2. Copy/move the ./include folder to the ```$DIR``` directory. 

In doing (2), the protoc executable will be able to find the well-known types (as it will be in ```../include```, relative to the executable). 

#### Compile the protobuf interfaces to your desired language
We will assume you are dealing in Python by default, since this whole project is Python-based. However, if you need to implement a particular component (e.g. DeviceController) in a different language, modify the below instructions for your required language.

``` sh
  cd /path/to/afspm/afspm/
  protoc --proto_path=./io/protos/src --python_out=./io/protos/generated/ ./io/protos/src/*.proto
  # Fix absolute to relative imports
  # Linux/Mac OSX:
  sed -i ./io/protos/generated/*_pb2.py -e 's/^import [^ ]*_pb2/from . \0/'
  # Windows (PowerShell):
  gci ./io/protos/generated/*_pb2.py -recurse | ForEach-Object { (Get-Content $_) | ForEach-Object { $_ -replace "^(import [^ ]*_pb2)", "from . `$0" } | Set-Content $_ }
```

### Unit testing

You can run unit tests (to validate the installation or otherwise) by running pytest:

``` sh
poetry run pytest
```

All tests should pass.

## Basic Usage

afspm is designed around the concept of a single TOML configuration file per experiment, within which a user defines:
- The communication protocols used between components.
- Common variables passed between components (e.g. how big the scan size will be).
- The components to spawn.

You can review some sample experiments in the samples directory.

### Starting up an experiment

To start an experiment, you can call the 'spawn' command with a provided config file. Additionally, you can define the subset of components you wish to spawn; this allows components to be separated on different computers (if desired).

You can find out the expected arguments for spawn by calling:

``` sh
spawn --help  # Already in virtual environment
poetry run spawn --help  # Outside of virtual environment
```

Note: if your experiment depends on local scripts, ensure you call your script within that directory (e.g. if you have an 'experiment.py' you will use, ensure it is in the directory where you call spawn; this assumes any class within is defined as "class = 'experiment.className' in your config.toml).

Whenever spawn is run, a components monitor is created to monitor all spawned components. This monitor communicates with components over an ipc connection, expecting heartbeats at a regular cadence. If a component does not send a heartbeat in a pre-alloted time, the monitor assumes the component has crashed or is frozen, and proceeds to kill and restart it. Upon restart, 'sufficient' prior data sent to the component will be resent via a cache mechanism within the afspm controller, allowing the component to return to the previous state it was in and continue functioning. The definition of 'sufficient' data is defined by the user in their configuration file. In some cases, it may be as simple as sending the last scan, scan parameters, scan state, etc. However, for more complicated logic, a more involved cache will be necessary.

This logic was put into place to try to minimize a crash breaking an experiment. Since individual SPM runs can be long (>8 hours), it is likely a run will be done while the user is at home (assuming any needed automation is in place). Thus, we do not want an unexpected crash of one component to ruin the experiment.

### Logging

afspm integrates with Python's logger functionality, to log communication between components as they happen. For any created components, hooking into this logger functionality can be done by creating a logger and prepending the afspm =LOGGER_ROOT= string to its name (found in spawn.py).

### Imaging and Visualization Libraries

While afspm uses its own abstraction for passing scan data between components, it contains converters from this Scan2d abstraction to pycroscopy's sidpy image format, and xarray's image format. Both of these libraries contain useful functionality, including visualization (via matplotlib).

## Limitations

As of today, we are not handling spectroscopic data. This will likely come in a future update.
