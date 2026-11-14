#!/usr/bin/env python3

# Read & write configuration files and provide default values.
#
# Licensed under the terms of the GPL2, see LICENSE file.
# Copyright (c) 2022 Julian Heinzel <jeinzi@gmx.de>

import os
import copy
import json


# config.json lives next to the scripts, so the CLI and the GUI share it no
# matter which directory they are started from. SMTCUT_CONFIG overrides it.
configFilePath = os.environ.get(
    "SMTCUT_CONFIG",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json"))

defaultConfig = {
    "inputPath": "",
    "outputPath": "result.gpgl",
    "gerbvPath": {
        "posix": "gerbv",
        "nt": "C:/Program Files (x86)/gerbv-2.6.0/bin/gerbv.exe"
    },
    "ghostscriptPath": {
        "posix": "gs",
        "nt": "C:/Program Files/gs/gs9.56.1/bin/gswin64.exe"
    },
    "pstoeditPath": {
        "posix": "pstoedit",
        "nt": "C:/Program Files/pstoedit/pstoedit.exe"
    },
    "offset": [0.5,0.5],
    "border": [1,1],
    "matrix": [1,0,0,1],
    "speed": [2,2],
    "force": [8,30],
    "mergeThreshold": [0.012, 0.01],
    "mediaSize": [12,11],
    "merge": 0,
    "cutMode": 0,
    "deviceName": {
        "posix": "/dev/usb/lp0",
        "nt": "\\\\[Computer Name]\\[Shared Name]"
    }
}




def getDefaultConfig():
    """Create a configuration object for a chosen operating system.

    Returns:
        dict: A dictionary containing configuration key/value pairs.
    """
    config = copy.deepcopy(defaultConfig)
    for key, value in config.items():
        if type(value) is dict:
            config[key] = value[os.name]
    return config




def readConfigFile(path):
    """Read and parse the config file as a dictionary.

    Args:
        path (string, optional): The path to the configuration file.

    Returns:
        dict: The config file as a dictionary.
    """
    config = {}
    try:
        with open(path) as configFile:
            config = json.load(configFile)
    except FileNotFoundError as e:
        print(f"Configuration file '{e.filename}' can't be found.")
    except json.decoder.JSONDecodeError:
        print(f"Can't decode configuration file '{path}'.")
        exit()
    return config




def writeConfigFile(config, path=None):
    """Dump config object to file.

    Args:
        config (dict): Config key/value pairs.
        path (string, optional): Path to the config file.
                                 Defaults to internal default value.
    """
    # The string replacements are just there to remove unnecessary
    # trailing zeros, to improve readability.
    path = path or configFilePath
    jsonString = json.dumps(config, indent=4).replace(".0,", ",").replace(".0\n", "\n")
    with open(path, "w") as file:
        file.write(jsonString)




def getConfig(path=None):
    """Get configuration settings from file with fallback to default values.

    Args:
        path (string, optional): The path to the config file.
                                 Defaults to internal default value.

    Returns:
        dict: Configuration settings.
    """
    config = getDefaultConfig()
    fileConfig = readConfigFile(path or configFilePath)
    # Use values from config file where defined, otherwise use default values.
    config.update(fileConfig)
    return config




def getMaterials(path=None):
    """Saved cutting settings per stencil material, from the "materials"
    section of the config file (written by "smtcut.py --save_material NAME").

    Returns:
        dict: material name -> dict of settings.
    """
    path = path or configFilePath
    if not os.path.isfile(path):
        return {}
    return readConfigFile(path).get("materials", {})




def saveMaterial(name, settings, path=None):
    """Store settings for a material in the config file, keeping everything
    else in the file as it is.

    Args:
        name (string): Material name, e.g. "duralar5".
        settings (dict): Setting name -> value.
        path (string, optional): The path to the config file.
    """
    path = path or configFilePath
    fileConfig = readConfigFile(path) if os.path.isfile(path) else {}
    fileConfig.setdefault("materials", {})[name] = settings
    writeConfigFile(fileConfig, path)
