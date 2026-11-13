"""HTTP routes for Send to Sogni and its render queue."""
from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any

from aiohttp import web

from . import sogni
from .media import STORE as MEDIA_STORE, MediaError, parse_session_id
from .sogni_models import (
    NETWORKS, RESOLUTIONS, TIERS, SogniOptionError, assign_media, canvas_size, clamp_duration,
    frame_ratio, model_id, parse_ratio,
)
from .sogni_queue import DISPATCHER, STORE as QUEUE

MAX_BATCH = 50


def _error(code: str, message: str, status: int = 400, details: Any = None) -> web.Response:
    payload: dict[str, Any] = {"error": {"code": code, "message": message}}
    if details is not None:
        payload["error"]["details"] = details
    return web.json_response(payload, status=status)


async def _body(request: web.Request) -> dict:
    try:
        body = await request.json()
    except (ValueError, TypeError):
        body = None
    if not isinstance(body, dict):
        raise sogni.SogniError("INVALID_REQUEST", "Expected a JSON object.")
    return body


def session_media(session_id: str | None, mode: str) -> list[dict[str, Any]]:
    """Writer's ready media for ``mode`` in reference order, with the file Sogni should get."""
    if mode == "T2VA" or not session_id:
        return []
    try:
        session = parse_session_id(session_id)
    except (ValueError, TypeError):
        raise sogni.SogniError("INVALID_REQUEST", "Invalid media session.") from None
    items = []
    for asset in MEDIA_STORE.sessions.get(session, []):
        if asset.get("mode") != mode or asset.get("status") == "needs_edit":
            continue
        path = asset.get("_edited_path") or asset.get("_original_path")
        if not path or not Path(path).exists():
            raise sogni.SogniError("MEDIA_MISSING", f"{asset.get('filename', 'A reference file')} is no longer available; add it again.")
        items.append({"type": asset["type"], "path": path, "width": asset.get("width"), "height": asset.get("height"), "duration": asset.get("duration"),
                      "reference": asset.get("reference"), "filename": asset.get("filename")})
    return items


def build_job(body: dict[str, Any], settings: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    prompt = str(body.get("prompt") or "").strip()
    if not prompt:
        raise sogni.SogniError("EMPTY_PROMPT", "There is no prompt to send.")
    if len(prompt) > 20000:
        raise sogni.SogniError("PROMPT_TOO_LONG", "The prompt is too long for Sogni.")
    mode = str(body.get("mode") or "T2VA")
    network = body.get("network") if body.get("network") in NETWORKS else settings["network"]
    tier = body.get("tier") if body.get("tier") in TIERS else settings["tier"]
    resolution = body.get("resolution") if body.get("resolution") in RESOLUTIONS else settings["resolution"]
    media = session_media(body.get("session_id"), mode)
    try:
        assign_media(mode, media)  # validate counts before anything is copied
        chosen_model = model_id(mode, tier, resolution)
    except SogniOptionError as error:
        raise sogni.SogniError("INVALID_OPTIONS", str(error)) from None
    width, height = canvas_size(frame_ratio(mode, media, parse_ratio(body.get("aspect_ratio"))), resolution)
    title = re.sub(r"\s+", " ", str(body.get("title") or prompt))[:80].strip()
    job = {
        "title": title, "prompt": prompt, "mode": mode, "network": network, "tier": tier,
        "resolution": resolution, "model_id": chosen_model, "width": width, "height": height,
        "duration": clamp_duration(body.get("duration")), "billing_mode": settings["billing_mode"],
        "tab_id": str(body.get("tab_id") or "")[:64] or None,
        "loras": settings["loras"], "content_filter": settings["content_filter"],
    }
    return job, media


def enqueue(body: dict[str, Any]) -> dict[str, Any]:
    job, media = build_job(body, sogni.current())
    record = QUEUE.add(job, media)
    DISPATCHER.poke()
    return QUEUE.public(record)


def listing() -> dict[str, Any]:
    return {"jobs": [QUEUE.public(job) for job in QUEUE.snapshot()], **DISPATCHER.overview()}


def register_sogni_routes(routes, prefix: str) -> None:
    base = f"{prefix}/sogni"

    def guarded(handler):
        async def wrapper(request: web.Request) -> web.Response:
            try:
                return await handler(request)
            except sogni.SogniError as error:
                return _error(error.code, error.message, error.status, error.details)
            except MediaError as error:
                return _error(error.code, error.message)
        wrapper.__name__ = handler.__name__
        return wrapper

    @routes.get(f"{base}/settings")
    @guarded
    async def get_sogni_settings(_request):
        return web.json_response(await asyncio.to_thread(sogni.public_settings))

    @routes.post(f"{base}/settings")
    @guarded
    async def post_sogni_settings(request):
        result = await asyncio.to_thread(sogni.save_settings, await _body(request))
        DISPATCHER.poke()
        return web.json_response(result)

    @routes.get(f"{base}/loras")
    @guarded
    async def get_sogni_loras(_request):
        return web.json_response(await asyncio.to_thread(DISPATCHER.call, DISPATCHER.lora_catalog, 90))

    @routes.get(f"{base}/jobs")
    @guarded
    async def get_sogni_jobs(_request):
        return web.json_response(listing())

    @routes.post(f"{base}/jobs")
    @guarded
    async def post_sogni_job(request):
        job = await asyncio.to_thread(enqueue, await _body(request))
        return web.json_response({"job": job, **listing()})

    @routes.post(f"{base}/jobs/batch")
    @guarded
    async def post_sogni_batch(request):
        items = (await _body(request)).get("items")
        if not isinstance(items, list) or not items:
            raise sogni.SogniError("INVALID_REQUEST", "Expected a non-empty list of prompts.")
        added, errors = [], []
        for index, item in enumerate(items[:MAX_BATCH]):
            try:
                added.append(await asyncio.to_thread(enqueue, item if isinstance(item, dict) else {}))
            except sogni.SogniError as error:
                errors.append({"index": index, "title": str((item or {}).get("title") or "")[:60], "message": error.message})
        return web.json_response({"added": added, "errors": errors, **listing()})

    @routes.post(f"{base}/jobs/clear-finished")
    @guarded
    async def post_sogni_clear(_request):
        removed = await asyncio.to_thread(QUEUE.clear_finished)
        return web.json_response({"removed": removed, **listing()})

    @routes.post(f"{base}/resume")
    @guarded
    async def post_sogni_resume(_request):
        DISPATCHER.resume()
        return web.json_response(listing())

    @routes.post(f"{base}/jobs/{{job_id}}/{{action}}")
    @guarded
    async def post_sogni_job_action(request):
        job_id, action = request.match_info["job_id"], request.match_info["action"]
        if QUEUE.get(job_id) is None:
            raise sogni.SogniError("NOT_FOUND", "That Sogni job is no longer in the queue.", status=404)
        if action == "cancel":
            await asyncio.to_thread(DISPATCHER.call, lambda: DISPATCHER.cancel(job_id))
        elif action == "retry":
            DISPATCHER.retry(job_id)
        elif action == "remove":
            if not QUEUE.remove(job_id):
                raise sogni.SogniError("JOB_ACTIVE", "Cancel the job before removing it.")
        elif action == "network":
            DISPATCHER.set_network(job_id, (await _body(request)).get("network"))
        elif action == "move":
            if not QUEUE.move(job_id, (await _body(request)).get("before")):
                raise sogni.SogniError("JOB_NOT_WAITING", "Only waiting jobs can be moved.")
        else:
            raise sogni.SogniError("INVALID_REQUEST", "Unknown job action.", status=404)
        return web.json_response(listing())

    @routes.get(f"{base}/jobs/{{job_id}}/thumb")
    async def get_sogni_job_thumb(request):
        job = QUEUE.get(request.match_info["job_id"])
        if job is None:
            return web.Response(status=404)
        output = sogni.output_root() / Path(job["saved_name"]).name if job.get("saved_name") else None
        thumb = await asyncio.to_thread(QUEUE.thumbnail, job["id"], output)
        if thumb is None:
            return web.Response(status=404)
        return web.FileResponse(thumb, headers={"Cache-Control": "max-age=86400"})

    # Pick up jobs that were queued or rendering before ComfyUI restarted.
    if any(job["status"] not in {"completed", "failed", "cancelled"} for job in QUEUE.snapshot()):
        DISPATCHER.start()
