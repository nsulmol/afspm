# gxsm Microscope Translator Guide

gxsm provides a python scripting environment, making it relatively simple to integrate into afspm. However, there are some caveats worth mentioning/knowing.

## Design

gxsm allows python scripting via an embedded python interpreter. This interpreter imports internal modules 'gxsm' (the calling API) and 'redirection' (to redirect stdio within the UI).

### Multiprocessing

Spawning new processes is not supported, so multiprocessing cannot be used. This is because the new process will not have pre-loaded the internal modules. Since afspm relies on multiprocessing to spawn individual components, this means we must spawn the gxsm MicroscopeTranslator *independent* of our main spawn call.

Because of this, we use ```spawn_monitorless_component``` instead of ```spawn_components``` to startup gxsm, and this is done within the pyremote console in the gxsm user interface. The rest of your experiment should be spawned in the standard method. To somewhat simplify this, we provide a helper module spawn_gxsm.py (located at afspm/components/microscope/translators/gxsm).

### Proper importing

As a general precaution, it is recommended to have an 'import gxsm' in any module that will call gxsm methods. This should only be required for people adding to the GxsmTranslator (by, for example, adding new modules that it relies on).

## How to Use

With an experiment defined in a TOML file:
- Startup gxsm from *within* your python environment, in the directory where you TOML file is located:
```bash
cd /path/to/experiment/file
poetry run gxsm3
```
- Select GxsmTranslator by selecting spawn_gxsm.py from the pyremote window's 'Open Python Script' option (top-right button in the window, select 'Open'). Start it up by clicking on the 'Execute Script' button (one left of 'Open' button, looks like a gear). You should see logging messages in the output windows, indicating it has started up successfully.
- Start the rest of your experiment by calling it in a separate terminal, excluding the microscope translator:
```bash
cd /path/to/experiment/file
poetry run spawn --components_not_to_spawn=['translator']
```
(This assumes 'translator' is your GxsmTranslator key in your TOML).

NOTE: Depending on your components, you may want to separately startup your MicroscopeScheduler first, to store any messages sent on startup in its cache. This can be done by calling, in a separate terminal:

```bash
cd /path/to/experiment/file
poetry run spawn config.toml --components_to_spawn=['scheduler']
```

, and then adding 'afspmcon' to your list of components *not* to spawn when spawning the rest of your experiment.

(This assumes 'scheduler' is your MicroscopeScheduler key in your TOML).

## How to Run Tests

You can test this MicroscopeTranslator by running test_translator.py. Look at the description in that module for more info.

