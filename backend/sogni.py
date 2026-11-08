"""Settings for Send to Sogni.

The API key stays on this machine: it is read from ``SOGNI_API_KEY`` or from a
settings file in the ComfyUI user directory, and it is never returned to the
browser. Jobs default to ``billing_mode="subscription"`` so a render that an
Unlimited plan cannot cover fails instead of spending Spark tokens.
"""
from __future__ import annotations

import json
import os
import threading
import urllib.request
import uuid
from pathlib import Path
from typing import Any

import folder_paths

from .sogni_models import NETWORKS, RESOLUTIONS, TIERS, options_catalog
from .version import VERSION


USER_AGENT = f"H3-Prompt-Writer/{VERSION}"
DOWNLOAD_TIMEOUT_S = 300
BILLING_MODES = ("subscription", "auto", "tokens")
MAX_SLOTS = 24
MAX_LORAS = 8
DEFAULT_SETTINGS = {
    "network": "fast",
    "tier": "fasth3",
    "resolution": "768p",
    "billing_mode": "subscription",
    # Unlimited: 1-2 concurrent videos and up to 8 queued; Sogni answers 4079 beyond that.
    "fast_slots": 2,
    "relaxed_slots": 8,
    "fallback_to_relaxed": False,
    "auto_send_queue": False,
    # Sogni's Sensitive Content Filter; when on, flagged renders finish without a video.
    "content_filter": True,
    "loras": [],
}
_LOCK = threading.RLock()


class SogniError(Exception):
    def __init__(self, code: str, message: str, *, status: int = 400, details: Any = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.details = details


def user_root() -> Path:
    getter = getattr(folder_paths, "get_user_directory", None)
    base = Path(getter()) if callable(getter) else Path(folder_paths.get_temp_directory())
    return base / "h3_prompt_writer"


SETTINGS_PATH = user_root() / "sogni.json"


def output_root() -> Path:
    return Path(folder_paths.get_output_directory()) / "h3_sogni"


def _read_file(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_file(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _int(value: Any, default: int, low: int, high: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, number))


def _loras(value: Any) -> list[dict[str, Any]]:
    """The LoRA stack sent with every render: Sogni catalog ids with strengths."""
    stack, seen = [], set()
    for item in value if isinstance(value, list) else []:
        lora_id = str(item.get("id") or "").strip() if isinstance(item, dict) else ""
        if not lora_id or len(lora_id) > 128 or lora_id in seen:
            continue
        try:
            strength = max(-100.0, min(100.0, float(item.get("strength", 1))))
        except (TypeError, ValueError):
            continue
        seen.add(lora_id)
        stack.append({"id": lora_id, "name": str(item.get("name") or lora_id)[:80], "strength": round(strength, 3)})
    return stack[:MAX_LORAS]


def normalize(raw: dict[str, Any]) -> dict[str, Any]:
    settings = dict(DEFAULT_SETTINGS)
    for key, allowed in (("network", NETWORKS), ("tier", TIERS), ("resolution", RESOLUTIONS), ("billing_mode", BILLING_MODES)):
        if raw.get(key) in allowed:
            settings[key] = raw[key]
    settings["fast_slots"] = _int(raw.get("fast_slots"), settings["fast_slots"], 1, MAX_SLOTS)
    settings["relaxed_slots"] = _int(raw.get("relaxed_slots"), settings["relaxed_slots"], 1, MAX_SLOTS)
    for key in ("fallback_to_relaxed", "auto_send_queue", "content_filter"):
        if isinstance(raw.get(key), bool):
            settings[key] = raw[key]
    settings["loras"] = _loras(raw.get("loras"))
    return settings


def api_key(path: Path = SETTINGS_PATH) -> tuple[str, str]:
    key = str(_read_file(path).get("api_key") or "").strip()
    if key:
        return key, "settings"
    env_key = os.environ.get("SOGNI_API_KEY", "").strip()
    if env_key:
        return env_key, "environment"
    return "", "none"


def app_id(path: Path = SETTINGS_PATH) -> str:
    """A stable per-installation id; Sogni socket clients must reuse it across restarts."""
    with _LOCK:
        raw = _read_file(path)
        value = str(raw.get("app_id") or "")
        if not value:
            value = f"h3-prompt-writer-{uuid.uuid4()}"
            _write_file(path, {**raw, "app_id": value})
        return value


def current(path: Path = SETTINGS_PATH) -> dict[str, Any]:
    with _LOCK:
        return normalize(_read_file(path))


def public_settings(path: Path = SETTINGS_PATH) -> dict[str, Any]:
    with _LOCK:
        raw = _read_file(path)
        key, source = api_key(path)
    return {
        **normalize(raw),
        "key_configured": bool(key),
        "key_source": source,
        "key_hint": f"…{key[-4:]}" if len(key) >= 8 else "",
        "billing_modes": list(BILLING_MODES),
        "max_slots": MAX_SLOTS,
        "max_loras": MAX_LORAS,
        **options_catalog(),
    }


def save_settings(body: dict[str, Any], path: Path = SETTINGS_PATH) -> dict[str, Any]:
    with _LOCK:
        raw = _read_file(path)
        updated = {**raw, **normalize({**normalize(raw), **body})}
        if "api_key" in body:
            key = str(body.get("api_key") or "").strip()
            if key and (len(key) > 512 or any(ch.isspace() for ch in key)):
                raise SogniError("INVALID_KEY", "The Sogni API key looks malformed.")
            if key:
                updated["api_key"] = key
            else:
                updated.pop("api_key", None)
        _write_file(path, updated)
    return public_settings(path)


def download(url: str, target: Path, opener=urllib.request.urlopen) -> None:
    if not url.startswith("https://"):
        raise SogniError("SOGNI_BAD_RESPONSE", "Sogni returned a non-HTTPS result link.", status=502)
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".part")
    with opener(request, timeout=DOWNLOAD_TIMEOUT_S) as response, open(tmp, "wb") as handle:
        while chunk := response.read(1 << 20):
            handle.write(chunk)
    os.replace(tmp, target)
