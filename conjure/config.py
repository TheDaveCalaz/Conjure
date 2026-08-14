"""Small persisted config so the guided workflow doesn't make you re-browse
the same files and re-type the same ID floors every time you port a model.

Purely a convenience — never raises on a missing/corrupt/unwritable file,
it just falls back to an empty config.
"""

import json
import os
import sys

CONFIG_FILENAME = "conjure_config.json"


def default_config_path() -> str:
    if getattr(sys, "frozen", False):
        base = os.path.dirname(sys.executable)
    else:
        main_module = sys.modules.get("__main__")
        main_file = getattr(main_module, "__file__", None)
        base = os.path.dirname(os.path.abspath(main_file)) if main_file else os.getcwd()
    return os.path.join(base, CONFIG_FILENAME)


class Config:
    def __init__(self, path: str = None):
        self.path = path or default_config_path()
        self.data = {}
        self.load()

    def load(self):
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                self.data = json.load(f)
            if not isinstance(self.data, dict):
                self.data = {}
        except (OSError, ValueError):
            self.data = {}

    def save(self):
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self.data, f, indent=2, sort_keys=True)
        except OSError:
            pass

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value):
        self.data[key] = value
