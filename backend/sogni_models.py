"""MiniMax H3 model ids, canvas sizes and SDK parameters for Send to Sogni.

Pure functions only, so they can be tested without the Sogni SDK installed.
"""
from __future__ import annotations

import math
from typing import Any

NETWORKS = ("fast", "relaxed")
TIERS = {
    "fasth3": "FastH3 Turbo (fastest)",
    "turbo": "Turbo (LightX2V)",
    "balanced": "Balanced",
    "standard": "Standard (best quality)",
}
RESOLUTIONS = {
    "480p": "480p",
    "768p": "768p",
    "1080p": "1080p (2-stage)",
    "2k": "2K (2-stage)",
}
TWO_STAGE = {"1080p", "2k"}

# Sogni model id per (tier, workflow). L2VA uses the I2V model with only an end frame.
_MODEL_IDS = {
    "fasth3": {
        "t2v": "minimax-h3-fastvideo-int8_t2v_turbo",
        "i2v": "minimax-h3-fastvideo-int8_i2v_turbo",
        "flf2v": "minimax-h3-fastvideo-int8_flf2v_turbo",
        "r2v": "minimax-h3-fastvideo-omni-int8_r2v_turbo",
    },
    "turbo": {
        "t2v": "minimax-h3-fl2va-fp8_t2v_turbo",
        "i2v": "minimax-h3-fl2va-fp8_i2v_turbo",
        "flf2v": "minimax-h3-fl2va-fp8_flf2v_turbo",
        "r2v": "minimax-h3-ref2va-fp8_r2v_turbo",
    },
    "balanced": {
        "t2v": "minimax-h3-fl2va-fp8_t2v_balanced",
        "i2v": "minimax-h3-fl2va-fp8_i2v_balanced",
        "flf2v": "minimax-h3-fl2va-fp8_flf2v_balanced",
        "r2v": "minimax-h3-ref2va-fp8_r2v_balanced",
    },
    "standard": {
        "t2v": "minimax-h3-fl2va-fp8_t2v",
        "i2v": "minimax-h3-fl2va-fp8_i2v",
        "flf2v": "minimax-h3-fl2va-fp8_flf2v",
        "r2v": "minimax-h3-ref2va-fp8_r2v",
    },
}
# Two-stage ids exist for every FastH3 workflow and for Standard/Balanced Reference.
_TWO_STAGE_IDS = {
    "minimax-h3-fastvideo-int8_t2v_turbo": "minimax-h3-fastvideo-int8_t2v_turbo_2stage",
    "minimax-h3-fastvideo-int8_i2v_turbo": "minimax-h3-fastvideo-int8_i2v_turbo_2stage",
    "minimax-h3-fastvideo-int8_flf2v_turbo": "minimax-h3-fastvideo-int8_flf2v_turbo_2stage",
    "minimax-h3-fastvideo-omni-int8_r2v_turbo": "minimax-h3-fastvideo-omni-int8_r2v_turbo_2stage",
    "minimax-h3-ref2va-fp8_r2v_balanced": "minimax-h3-ref2va-fp8_r2v_balanced_2stage",
    "minimax-h3-ref2va-fp8_r2v": "minimax-h3-ref2va-fp8_r2v_2stage",
}
MODE_WORKFLOW = {"T2VA": "t2v", "I2VA": "i2v", "FL2VA": "flf2v", "L2VA": "i2v", "Reference": "r2v"}
SUPPORTED_MODES = tuple(MODE_WORKFLOW)

# Canvas pixel budgets. Two-stage output is delivered at twice the canvas width and height.
_GRID = 32
_MAX_SIDE = 1344
_MAX_PIXELS = 1_032_192
_BUDGET = {"480p": 832 * 480, "768p": _MAX_PIXELS, "1080p": 960 * 544, "2k": _MAX_PIXELS}
MIN_DURATION_S = 5
MAX_DURATION_S = 15


class SogniOptionError(ValueError):
    pass


def all_model_ids() -> set[str]:
    ids = {model for workflows in _MODEL_IDS.values() for model in workflows.values()}
    return ids | set(_TWO_STAGE_IDS.values())


def model_id(mode: str, tier: str, resolution: str) -> str:
    workflow = MODE_WORKFLOW.get(mode)
    if workflow is None:
        raise SogniOptionError(f"Send to Sogni does not support {mode}.")
    if tier not in _MODEL_IDS:
        raise SogniOptionError(f"Unknown H3 tier {tier!r}.")
    base = _MODEL_IDS[tier][workflow]
    if resolution in TWO_STAGE:
        two_stage = _TWO_STAGE_IDS.get(base)
        if not two_stage:
            raise SogniOptionError(
                f"{RESOLUTIONS[resolution]} needs FastH3 Turbo"
                + (" or Standard/Balanced in Reference mode." if workflow == "r2v" else ".")
            )
        return two_stage
    return base


def parse_ratio(value: Any) -> float:
    if isinstance(value, (int, float)) and value > 0:
        return float(value)
    try:
        width, height = (float(part) for part in str(value).split(":", 1))
    except (TypeError, ValueError):
        return 16 / 9
    return width / height if width > 0 and height > 0 else 16 / 9


def canvas_size(ratio: float, resolution: str) -> tuple[int, int]:
    """Largest 32 px grid canvas within the H3 limits that matches ``ratio`` within ~3%."""
    budget = _BUDGET.get(resolution, _MAX_PIXELS)
    ratio = min(max(ratio, 0.25), 4.0)
    best: tuple[int, float, int, int] | None = None
    for height in range(_GRID, _MAX_SIDE + 1, _GRID):
        for width in {max(_GRID, math.floor(height * ratio / _GRID) * _GRID), max(_GRID, math.ceil(height * ratio / _GRID) * _GRID)}:
            if width > _MAX_SIDE or width * height > budget:
                continue
            error = abs(math.log((width / height) / ratio))
            # Largest area wins; among equal areas the closer shape wins.
            if error <= 0.03 and (best is None or (width * height, -error) > (best[0], best[1])):
                best = (width * height, -error, width, height)
    if best is None:  # Extreme ratios: fall back to the closest shape that fits.
        for height in range(_MAX_SIDE, _GRID - 1, -_GRID):
            width = min(_MAX_SIDE, max(_GRID, round(height * ratio / _GRID) * _GRID))
            if width * height <= budget:
                return width, height
    return best[2], best[3]


def clamp_duration(seconds: Any) -> int:
    try:
        value = int(round(float(seconds)))
    except (TypeError, ValueError):
        value = MIN_DURATION_S
    return max(MIN_DURATION_S, min(MAX_DURATION_S, value))


def assign_media(mode: str, media: list[dict[str, Any]]) -> dict[str, Any]:
    """Map Writer's ordered media to the SDK upload fields.

    ``media`` items carry ``type`` (image/video/audio) and ``path``, in Writer's
    reference order, so ``<Picture 1>`` stays the first image and so on.
    """
    images = [m["path"] for m in media if m["type"] == "image"]
    videos = [m["path"] for m in media if m["type"] == "video"]
    audios = [m["path"] for m in media if m["type"] == "audio"]
    fields: dict[str, Any] = {}
    if mode == "T2VA":
        return fields
    if mode == "I2VA":
        if len(images) != 1:
            raise SogniOptionError("I2VA needs exactly one start image.")
        fields["referenceImage"] = images[0]
    elif mode == "L2VA":
        if len(images) != 1:
            raise SogniOptionError("L2VA needs exactly one last-frame image.")
        fields["referenceImageEnd"] = images[0]
    elif mode == "FL2VA":
        if len(images) != 2:
            raise SogniOptionError("FL2VA needs a first and a last frame.")
        fields["referenceImage"], fields["referenceImageEnd"] = images
    elif mode == "Reference":
        if not images and not videos:
            raise SogniOptionError("Reference mode needs at least one image or video.")
        if len(images) > 9 or len(videos) > 3 or len(audios) > 3 or len(images) + len(videos) + len(audios) > 12:
            raise SogniOptionError("Reference mode allows up to 9 images, 3 videos and 3 audio files (12 total).")
        if images:
            fields["referenceImage"] = images[0]
            if images[1:]:
                fields["contextImages"] = images[1:]
        if videos:
            fields["referenceVideo"] = videos[0]
            if videos[1:]:
                fields["referenceVideos"] = videos[1:]
        if audios:
            fields["referenceAudio"] = audios[0]
            if audios[1:]:
                fields["referenceAudios"] = audios[1:]
    return fields


def frame_ratio(mode: str, media: list[dict[str, Any]], fallback: float) -> float:
    """Frame modes follow the shape of their first image; others use Writer's aspect ratio."""
    if mode in {"I2VA", "FL2VA", "L2VA"}:
        for item in media:
            if item["type"] == "image" and item.get("width") and item.get("height"):
                return item["width"] / item["height"]
    return fallback


def project_params(job: dict[str, Any]) -> dict[str, Any]:
    """SDK ``projects.create`` parameters for a queued job."""
    params: dict[str, Any] = {
        "type": "video",
        "modelId": job["model_id"],
        "positivePrompt": job["prompt"],
        "numberOfMedia": 1,
        "network": job["network"],
        "width": job["width"],
        "height": job["height"],
        "duration": job["duration"],
        "billingMode": job["billing_mode"],
    }
    if job.get("seed") is not None:
        params["seed"] = job["seed"]
    if job.get("loras"):
        params["loras"] = [lora["id"] for lora in job["loras"]]
        params["loraStrengths"] = [lora["strength"] for lora in job["loras"]]
    if job.get("content_filter") is False:
        params["disableNSFWFilter"] = True
    params.update(assign_media(job["mode"], job.get("media") or []))
    return params


def options_catalog() -> dict[str, Any]:
    return {
        "networks": list(NETWORKS),
        "tiers": [{"id": key, "label": label} for key, label in TIERS.items()],
        "resolutions": [{"id": key, "label": label} for key, label in RESOLUTIONS.items()],
        "modes": list(SUPPORTED_MODES),
        "duration_range": [MIN_DURATION_S, MAX_DURATION_S],
    }
