from pathlib import Path
import configparser
import sys

# if has param in config it is the path from base dir, otherwise it is the path from the current working directory
def load_config(path=None):

    if getattr(sys, "frozen", False):
        base_dir = Path(sys.executable).parent
    else:
        base_dir = Path(__file__).resolve().parent.parent
    # put parameter here
    if path:
        config_file = base_dir / path
    else:
        config_file = base_dir / "config.ini"



    if not config_file.exists():
        raise FileNotFoundError(
            f"Configuration file not found: {config_file}"
        )

    config = configparser.ConfigParser()
    config.read(config_file)

    return config