# Asylum Translator Installation Instructions

Asylum's UI is built on top of Wavemetrics' Igor Pro, via an Igor extension plugin called an XOR plugin. In order to interface with them via afspm, we use a zmq-based interface called ZeroMQ-XOP (developed at the AllenInstitute). With this, we send command requests (and associated parameters) via JSON structures which we transfer over a zmq socket. The zmq-xop interface receives them, passes them to IgorPro, and returns the results.

## Installation

Follow the instructions at https://github.com/AllenInstitute/ZeroMQ-XOP to install this special XOP module. Ensure you follow the special instructions for Igor Pro 6, as this is the version used by Asylum Research.

## Usage

Once installed, the Asylum controller can be interfaced with afspm via the following steps:
1. Start up the Asylum Research executable, AR.exe. This is based off of Igor Pro.
2. Select your scan mode (e.g. Air Topography), to set up your controller.
3. Ensure the Igor Command Window is open (either via Ctrl+J or selecting Windows->Command Window in the menu).
4. To communicate with afspm, we must start up a ZeroMQ client for the Asylum controller. This is achieved via the following calls (in the command window):
```
zeromq_stop() // Stop any existing ZeroMQ operations
zeromq_server_bind("tcp://127.0.0.1:5555") // Create a ZeroMQ server and begin listening
zeromq_handler_start() // Prepare to handle incoming messages
```
(Note that the zmq address provided in 'bind' must match that of the AsylumTranslator XOPClient's address, so they can connect).
5. Start your experiment via your config file in afspm:
```shell
poetry shell  # Open shell in virtual environment
spawn /path/to/config/config.toml
```

## Testing
To validate that the interface is working:
1. Run the example program in ZeroMQ-XOP to ensure the XOP is functioning.
2. Run the MicroscopeTranslator unit tests in afspm to ensure the Asylum translator is communicating properly with the Igor Pro software (via the ZeroMQ-XOP interface).

For (1), you want to call the example script with an expected function, to validate the return is what you expect. For example, to call a 'Get Value' on the parameter 'ScanSize', you would do:

```
zmq_xop_client.exe "tcp://127.0.0.1:5555" "{ \"version\" : 1, \"CallFunction\" : { \"name\" : \"GV\", \"params\" : [ \"ScanSize\"] } }"
```

(assuming the zmq node is 'tcp://127.0.0.1:5555').

## Notes

The commands used to set variables here *do not* perform the same safety checking that the Asylum control panel does. Particularly, we use the lowest-level 'PV()' methods ('Put Value'), while the control panel uses an Asylum-created FMapSetVar(). The issue is linked to the fact that FMapSetVar() takes a STRUCT as input. Unfortunately, the library we use to communicate with the Asylum/Igor does not support STRUCT parameters sent as input, so we are forced to use the less-safe method instead.

Please keep this limitation in mind when running experiments! It would be smart to test the sample settings you expect to set, to ensure they are within expected ranges.
