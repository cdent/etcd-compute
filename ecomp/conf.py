import io
import os

import yaml


def configure(defaults, config_file):
    """Read a configuration yaml file, if it exists, to override config
    defaults.
    """
    config = {}
    config.update(defaults)
    config_data = {}
    # let the possible exceptions bubble
    if os.path.exists(config_file):
        config_data = yaml.safe_load(io.open(config_file).read())
    config.update(config_data)
    return config
