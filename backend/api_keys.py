"""API provider keys saved in the ComfyUI user directory so a connection survives a restart.

Keys are stored per preset and base URL. Only the backend reads this file; the
browser receives a key hint, never the key.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import folder_paths

KEYS_PATH = Path(folder_paths.get_user_directory()) / "h3_prompt_writer" / "api_keys.json"


def _read() -> dict[str, str]:
    try:
        data = json.loads(KEYS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {slot: key for slot, key in data.items() if isinstance(key, str)} if isinstance(data, dict) else {}


def saved_key(preset: str, base_url: str) -> str:
    return _read().get(f"{preset}|{base_url}", "")


def save_key(preset: str, base_url: str, key: str) -> None:
    data = _read()
    if key:
        data[f"{preset}|{base_url}"] = key
    elif data.pop(f"{preset}|{base_url}", None) is None:
        return
    KEYS_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = KEYS_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, KEYS_PATH)
