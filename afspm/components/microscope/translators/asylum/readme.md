# Asylum Translator Installation Instructions

Asylum's UI is built on top of Wavemetrics' Igor Pro, via an Igor extension plugin called an XOR plugin. In order to interface with them via afspm, we use a zmq-based interface called ZeroMQ-XOP (developed at the AllenInstitute). With this, we send command requests (and associated parameters) via JSON structures which we transfer over a zmq socket. The zmq-xop interface receives them, passes them to IgorPro, and returns the results.

## Installation

### ZeroMQ-XOP Installation 

Follow the instructions at https://github.com/AllenInstitute/ZeroMQ-XOP to install this special XOP module. Ensure you follow the special instructions for Igor Pro 6, as this is the version used by Asylum Research.

In order for spectroscopy to work, we also need to copy a custom Igor Procedure file to the appropriate location. 

### Spectroscopy Support Installation

In order to send spectroscopy requests, we needed to implement our own top-level functions in Igor (in the Spectroscopy.ipf file). To allow the Igor program to read these, we need to copy them into the appropriate location:

```text
%USER_PROFILE%/Documents/WaveMetrics/Igor Pro 6 User Files/User Procedures
```

## Usage

On startup of an experiment, you will need to set up ZeroMQ-XOP, and load the Spectroscopy file.

Before doing any of this, we assume you have:
1. Start up the Asylum Research executable, AR.exe. This is based off of Igor Pro.
2. Select your scan mode (e.g. Air Topography), to set up your controller.

### ZeroMQ-XOP Setup

Once installed, the Asylum controller can be interfaced with afspm via the following steps:
1. Ensure the Igor Command Window is open (either via Ctrl+J or selecting Windows->Command Window in the menu).
2. To communicate with afspm, we must start up a ZeroMQ client for the Asylum controller. This is achieved via the following calls (in the command window):
```
zeromq_stop() // Stop any existing ZeroMQ operations
zeromq_server_bind("tcp://127.0.0.1:5555") // Create a ZeroMQ server and begin listening
zeromq_handler_start() // Prepare to handle incoming messages
```
(Note that the zmq address provided in 'bind' must match that of the AsylumTranslator XOPClient's address, so they can connect).

### Load Spectroscopy File

Next, we must tell Igor to include our methods from Spectroscopy.ipf. To do this, we:
1. Open the Procedure window (either via Ctrl+M or selecting Windows->Procedure Windows->Procedure Window).
2. In the opened window, place your cursor at the llast line of the Procedure file (i.e. after 'StartMeUp()').
3. Type the following:

```text
#include "Spectroscopy"
```

4. Click on the 'Compile' button on the bottom left toolbar. If it succeeds, the 'Compile' button will disappear.

### Starting Your Experiment

Start your experiment via your config file in afspm:
```shell
poetry run spawn /path/to/config/config.toml
```

## Testing

### ZeroMQ-XOP Validation
To validate that the interface is working:
1. Run the example program in ZeroMQ-XOP to ensure the XOP is functioning.
2. Run the MicroscopeTranslator unit tests in afspm to ensure the Asylum translator is communicating properly with the Igor Pro software (via the ZeroMQ-XOP interface).

For (1), you want to call the example script with an expected function, to validate the return is what you expect. For example, to call a 'Get Value' on the parameter 'ScanSize', you would do:

```
zmq_xop_client.exe "tcp://127.0.0.1:5555" '{ \"version\" : 1, \"CallFunction\" : { \"name\" : \"GV\", \"params\" : [ \"ScanSize\"] } }'
```

(assuming the zmq node is 'tcp://127.0.0.1:5555').

### Spectroscopy Validation

You can verify that it has succeeded by querying for the probe position's x-coordinate in the command window (Ctrl+J):
```text
print(GetProbePosX())
```

Upon pressing enter, you should see a value. Note that if you have not yet started the Asylum controller, you will get a pop-up error stating:

```text
Function Execution Error: While executing a wave read, the following error occurred: Index out of range for wave "SpotX".
```

This is expected (the appropriate variable will be set up by the Asylum translator on startup).

## Notes

### Parameter Range Support

The commands used to set variables here *do not* perform the same safety checking that the Asylum control panel does. Particularly, we use the lowest-level 'PV()' methods ('Put Value'), while the control panel uses an Asylum-created FMapSetVar(). The issue is linked to the fact that FMapSetVar() takes a STRUCT as input. Unfortunately, the library we use to communicate with the Asylum/Igor does not support STRUCT parameters sent as input, so we are forced to use the less-safe method instead.

Please keep this limitation in mind when running experiments! It would be smart to test the sample settings you expect to set, to ensure they are within expected ranges.

To ensure safe usage, the AsylumTranslator has built-in range checks. These use the parameter-specific ranges defined in the AsylumTranslator 'params.toml' file. If you did not modify/provide your own, it will be located at the following relative path:

```text
afspm/components/microscope/translators/asylum/params.toml
```

### Random Crash Igor

I have witnessed Igor/AR.exe crash while running an experiment. It sent no error code, and simply closed. I suspect this *might* have something to do with polling parameters too frequently for Igor's expectations.

I will look into this further and update once fixed.

### Spectroscopy Testing

When running test_translator, I set the 'Trigger Channel' in the Force panel to 'RawZSensor'. When set to the default, it does not work properly. I suspect this has to do with other parameters/settings one would be familiar with when using spectroscopies on Asylum. For testing purposes, it is sufficient to do the above change.

### X Offset / Y Offset UI Bug

When using afspm, changes to scan-top-left-x or scan-top-left-y show up with the set value (in 'm') in the Parms window in AR.exe, but the units indicate it as 'nm'. This is a UI bug: the actual stored value is proper (i.e. in 'm'), it is just the UI that improperly indicates it as being in 'nm'.

### Save Options Switching Between 'Image' and 'Force'

When alternating between saving scans and spectroscopies, the asylum translator will change the base name accordingly ('Image' for scans, 'Force' for spectroscopies). This is useful because both saves have the same extension. However, the change of basename only occurs when a spec or scan is started. Therefore, if the last save was a spec, the base name will be 'Force'. This may cause confusion for a user ending an automated experiment; please keep this in mind.
