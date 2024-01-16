# Asylum Controller Installation Instructions

Asylum's UI is built on top of Wavemetrics' Igor Pro, via an Igor extension plugin called an XOR plugin. In order to interface with them via afspm, we use a zmq-based interface called ZeroMQ-XOP (developed at the AllenInstitute). With this, we send command requests (and associated parameters) via JSON structures which we transfer over a zmq socket. The zmq-xop interface receives them, passes them to IgorPro, and returns the results.

## Installation

Follow the instructions at https://github.com/AllenInstitute/ZeroMQ-XOP to install this special XOP module. Ensure you follow the special instructions for Igor Pro 6, as this is the version used by Asylum Research.

## Usage

Once installed, the Asylum controller can be interfaced with afspm via the following steps:
1. Start up the Asylum Research executable, AR.exe. This is based off of Igor Pro.
2. To communicate with afspm, we must start up a ZeroMQ client for the Asylum controller. This is achieved via the following calls:
```
zeromq_stop() // Stop any existing ZeroMQ operations
zeromq_server_bind("tcp://127.0.0.1:5555") // Create a ZeroMQ server and begin listening
zeromq_handler_start() // Prepare to handle incoming messages
```
(Note that the zmq address provided in 'bind' must match that of the AsylumController XOPClient's address, so they can connect).
3. Start your experiment via your config file in afspm:
```shell
poetry shell  # Open shell in virtual environment
spawn /path/to/config/config.toml
```

## Testing
To validate that the interface is working:
1. Run the example program in ZeroMQ-XOP to ensure the XOP is functioning.
2. Run the DeviceController unit tests in afspm to ensure the Asylum device controller is communicating properly with the Igor Pro software (via the ZeroMQ-XOP interface).
