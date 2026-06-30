"""Mutable runtime settings the Telegram bot can change on the fly.

Holds the current Claude effort + model. Persisted to runtime.json under the
config dir so a change survives a restart. Initialized from config defaults.
"""

import json
import os

from . import config

_FILE = config.CONFIG_DIR / "runtime.json"

_DEFAULTS = {
    "effort": config.CLAUDE_EFFORT,
    "model": config.CLAUDE_MODEL,
}


def _load() -> dict:
    state = dict(_DEFAULTS)
    try:
        with open(_FILE) as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return state
    if isinstance(data, dict):
        for key in _DEFAULTS:
            value = data.get(key)
            if isinstance(value, str) and value:
                state[key] = value
    return state


_state = _load()


def _save() -> None:
    # Atomic write: a kill/disk-full mid-write can't corrupt or truncate the
    # existing file — we replace it only once the new copy is fully written.
    try:
        config.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        tmp = _FILE.with_suffix(".json.tmp")
        with open(tmp, "w") as fh:
            json.dump(_state, fh, indent=2)
        os.replace(tmp, _FILE)
    except OSError:
        pass


def get(key: str) -> str:
    return _state.get(key, _DEFAULTS.get(key))


def set(key: str, value: str) -> None:
    if key not in _DEFAULTS:
        raise KeyError(key)
    _state[key] = value
    _save()


def snapshot() -> dict:
    return dict(_state)
