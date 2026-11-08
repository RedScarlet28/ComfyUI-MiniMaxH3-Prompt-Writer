"""A persistent local render queue that feeds Sogni as plan slots free up.

Jobs are stored in the ComfyUI user directory together with copies of their
reference media, so they survive a ComfyUI restart. One background thread owns
an asyncio loop and a single Sogni SDK client. It keeps up to ``<network>_slots``
jobs in flight per network, backs off when Sogni reports that the plan queue is
full (4079), and pushes the next job in as soon as one finishes.
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable

from . import sogni
from .sogni_models import NETWORKS, all_model_ids, project_params

QUEUE_FULL = 4079
RENEWAL_RETRY = 4080
FAST_CAPACITY = {4087, 4089}
ADMISSION_WAIT_S = 20
LIVE_WAIT_S = 60
RECOVERED_POLL_S = 30
TICK_S = 2
IDLE_CLOSE_S = 300
QUEUE_FULL_BACKOFF_S = (30, 60, 120, 300)
MAX_SUBMIT_ATTEMPTS = 5
SENSITIVE_HINT = "Sogni's Sensitive Content Filter withheld the video. Turn the filter off in Send to Sogni settings, then Retry."
ACTIVE = {"submitting", "active"}
FINISHED = {"completed", "failed", "cancelled"}
PUBLIC_FIELDS = (
    "id", "title", "mode", "status", "network", "tier", "resolution", "model_id", "width", "height",
    "duration", "created_at", "submitted_at", "finished_at", "project_id", "message", "saved_name",
    "video_url", "attempts", "media_count", "waiting", "loras", "content_filter",
)


class GatewayError(Exception):
    def __init__(self, code: Any, message: str, *, retry_after: float | None = None, transient: bool = False):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retry_after = retry_after
        self.transient = transient


class JobStore:
    def __init__(self, root: Path):
        self.root = root
        self.path = root / "queue.json"
        self.lock = threading.RLock()
        self.jobs: list[dict[str, Any]] = []
        self.load()

    def load(self) -> None:
        with self.lock:
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                data = {}
            jobs = data.get("jobs") if isinstance(data, dict) else None
            self.jobs = [job for job in jobs or [] if isinstance(job, dict) and job.get("id")]
            for job in self.jobs:
                # A submit that never got a project id cannot be recovered; send it again.
                if job.get("status") == "submitting" and not job.get("project_id"):
                    job["status"] = "pending"

    def save(self) -> None:
        with self.lock:
            self.root.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"version": 1, "jobs": self.jobs}, indent=1), encoding="utf-8")
            os.replace(tmp, self.path)

    def job_dir(self, job_id: str) -> Path:
        return self.root / "jobs" / job_id

    def add(self, job: dict[str, Any], media: list[dict[str, Any]]) -> dict[str, Any]:
        job_id = uuid.uuid4().hex[:16]
        directory = self.job_dir(job_id)
        directory.mkdir(parents=True, exist_ok=True)
        copied = []
        try:
            for index, item in enumerate(media):
                source = Path(item["path"])
                target = directory / f"{index:02d}_{item['type']}{source.suffix.lower()}"
                shutil.copy2(source, target)
                copied.append({**item, "path": str(target)})
        except OSError as error:
            shutil.rmtree(directory, ignore_errors=True)
            raise sogni.SogniError("MEDIA_COPY_FAILED", f"Could not copy reference media: {error}") from None
        record = {
            **job, "id": job_id, "media": copied, "media_count": len(copied), "status": "pending",
            "created_at": time.time(), "attempts": 0, "message": "", "waiting": "",
        }
        with self.lock:
            self.jobs.append(record)
            self.save()
        return record

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self.lock:
            return next((job for job in self.jobs if job["id"] == job_id), None)

    def update(self, job_id: str, **fields: Any) -> dict[str, Any] | None:
        with self.lock:
            job = self.get(job_id)
            if job is None:
                return None
            job.update(fields)
            if fields.get("status") in FINISHED:
                job["finished_at"] = job.get("finished_at") or time.time()
            if fields.get("status") == "completed":
                # Failed and cancelled jobs keep their media so Retry can resend them.
                shutil.rmtree(self.job_dir(job_id), ignore_errors=True)
            self.save()
            return job

    def remove(self, job_id: str) -> bool:
        with self.lock:
            job = self.get(job_id)
            if job is None or job["status"] in ACTIVE:
                return False
            self.jobs.remove(job)
            shutil.rmtree(self.job_dir(job_id), ignore_errors=True)
            self.save()
            return True

    def clear_finished(self) -> int:
        with self.lock:
            finished = [job for job in self.jobs if job["status"] in FINISHED]
            for job in finished:
                self.jobs.remove(job)
                shutil.rmtree(self.job_dir(job["id"]), ignore_errors=True)
            self.save()
            return len(finished)

    def snapshot(self) -> list[dict[str, Any]]:
        with self.lock:
            return [dict(job) for job in self.jobs]

    @staticmethod
    def public(job: dict[str, Any]) -> dict[str, Any]:
        return {key: job.get(key) for key in PUBLIC_FIELDS}


class SdkGateway:
    """The Sogni SDK connection: submits over the socket, reads results over REST."""

    def __init__(self, key: str, app_id: str):
        self.key = key
        self.app_id = app_id
        self.client = None
        self.projects: dict[str, Any] = {}

    async def open(self) -> None:
        from sogni_client import SogniClient  # optional dependency, imported on first use

        self.client = await SogniClient.create(api_key=self.key, app_id=self.app_id, app_source="h3-prompt-writer")

    @staticmethod
    def _wrap(error: BaseException) -> GatewayError:
        code = getattr(error, "code", None) or getattr(error, "error_code", None)
        status = getattr(error, "status", None)
        transient = isinstance(error, (OSError, ConnectionError)) or status in {429, 502, 503, 504}
        return GatewayError(code, str(error) or type(error).__name__,
                            retry_after=getattr(error, "retry_after", None), transient=transient)

    async def submit(self, params: dict[str, Any]) -> tuple[str, list[str] | None]:
        try:
            project = await self.client.projects.create(params)
        except Exception as error:  # noqa: BLE001 - SDK raises several error types
            raise self._wrap(error) from None
        self.projects[project.id] = project
        try:
            # Admission refusals (queue full, capacity, model unavailable) arrive within seconds.
            urls = await project.wait_for_completion(timeout=ADMISSION_WAIT_S)
        except asyncio.TimeoutError:
            return project.id, None
        except Exception as error:  # noqa: BLE001
            self.projects.pop(project.id, None)
            raise self._wrap(error) from None
        return project.id, urls

    async def wait(self, project_id: str, timeout: float) -> list[str] | None:
        project = self.projects.get(project_id)
        if project is None:
            await asyncio.sleep(timeout)
            return None
        try:
            return await project.wait_for_completion(timeout=timeout)
        except asyncio.TimeoutError:
            return None
        except Exception as error:  # noqa: BLE001
            self.projects.pop(project_id, None)
            raise self._wrap(error) from None

    async def result(self, project_id: str) -> dict[str, Any]:
        try:
            return await self.client.projects.get_result(project_id, kind="video")
        except Exception as error:  # noqa: BLE001
            raise self._wrap(error) from None

    async def loras(self) -> list[dict[str, Any]]:
        try:
            catalog = await self.client.projects.available_loras({"includePersonal": True})
        except Exception:  # noqa: BLE001 - personal imports need an active subscription
            try:
                catalog = await self.client.projects.available_loras()
            except Exception as error:  # noqa: BLE001
                raise self._wrap(error) from None
        return catalog["loras"]

    async def cancel(self, project_id: str) -> None:
        try:
            await self.client.projects.cancel(project_id)
        except Exception as error:  # noqa: BLE001
            raise self._wrap(error) from None

    async def close(self) -> None:
        client, self.client = self.client, None
        self.projects.clear()
        if client is not None:
            await client.aclose()


def sdk_available() -> bool:
    try:
        import sogni_client  # noqa: F401
    except ImportError:
        return False
    return True


class Dispatcher:
    def __init__(self, store: JobStore, *, gateway_factory: Callable[[], Any] | None = None,
                 settings: Callable[[], dict[str, Any]] = sogni.current,
                 downloader: Callable[[str, Path], None] = sogni.download,
                 output_root: Callable[[], Path] = sogni.output_root,
                 clock: Callable[[], float] = time.time):
        self.store = store
        self.gateway_factory = gateway_factory or self._sdk_gateway
        self.settings = settings
        self.downloader = downloader
        self.output_root = output_root
        self.clock = clock
        self.gateway = None
        self.state = "idle"
        self.state_message = ""
        self.blocked_until = {network: 0.0 for network in NETWORKS}
        self.blocked_reason = {network: "" for network in NETWORKS}
        self.full_strikes = {network: 0 for network in NETWORKS}
        self.monitors: dict[str, asyncio.Task] = {}
        self.idle_since: float | None = None
        self.loop: asyncio.AbstractEventLoop | None = None
        self.wake: asyncio.Event | None = None
        self.thread: threading.Thread | None = None
        self.lock = threading.Lock()

    @staticmethod
    def _sdk_gateway() -> SdkGateway:
        if not sdk_available():
            raise sogni.SogniError("SOGNI_SDK_MISSING", "The Sogni SDK is not installed (pip install sogni-client).")
        key, _source = sogni.api_key()
        if not key:
            raise sogni.SogniError("SOGNI_NO_KEY", "Add your Sogni API key in Send to Sogni settings.")
        return SdkGateway(key, sogni.app_id())

    # ---- thread lifecycle -------------------------------------------------
    def start(self) -> None:
        with self.lock:
            if self.thread and self.thread.is_alive():
                return
            self.thread = threading.Thread(target=self._run_thread, name="h3-sogni-queue", daemon=True)
            self.thread.start()

    def _run_thread(self) -> None:
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.wake = asyncio.Event()
        self.loop.run_until_complete(self.run_forever())

    def poke(self) -> None:
        self.start()
        if self.loop and self.wake:
            self.loop.call_soon_threadsafe(self.wake.set)

    def call(self, coro_factory: Callable[[], Any], timeout: float = 30) -> Any:
        self.start()
        while self.loop is None:
            time.sleep(0.01)
        return asyncio.run_coroutine_threadsafe(coro_factory(), self.loop).result(timeout)

    async def run_forever(self) -> None:
        while True:
            try:
                await self.tick()
            except Exception as error:  # noqa: BLE001 - keep the worker alive
                self.state, self.state_message = "error", str(error)
            try:
                await asyncio.wait_for(self.wake.wait(), TICK_S)
            except asyncio.TimeoutError:
                pass
            self.wake.clear()

    # ---- scheduling -------------------------------------------------------
    async def ensure_gateway(self) -> bool:
        if self.gateway is not None:
            return True
        try:
            gateway = self.gateway_factory()
            await gateway.open()
        except sogni.SogniError as error:
            self.state, self.state_message = error.code, error.message
            return False
        except Exception as error:  # noqa: BLE001
            self.state, self.state_message = "SOGNI_UNREACHABLE", f"Could not connect to Sogni: {error}"
            return False
        self.gateway = gateway
        self.state, self.state_message = "connected", ""
        return True

    async def tick(self) -> None:
        jobs = self.store.snapshot()
        pending = [job for job in jobs if job["status"] == "pending"]
        active = [job for job in jobs if job["status"] in ACTIVE]
        now = self.clock()
        if not pending and not active:
            if self.gateway is not None:
                self.idle_since = self.idle_since or now
                if now - self.idle_since > IDLE_CLOSE_S:
                    await self.gateway.close()
                    self.gateway = None
            self.state = "idle" if self.gateway is None else "connected"
            self.state_message = ""
            return
        self.idle_since = None
        if not await self.ensure_gateway():
            return
        for job in active:
            if job["id"] not in self.monitors and job.get("project_id"):
                self.monitors[job["id"]] = asyncio.ensure_future(self.monitor(job["id"], job["project_id"], live=False))
        settings = self.settings()
        for network in NETWORKS:
            if self.blocked_until[network] > now:
                continue
            self.blocked_reason[network] = ""
            in_flight = sum(1 for job in self.store.snapshot() if job["status"] in ACTIVE and job["network"] == network)
            slots = settings[f"{network}_slots"]
            for job in [j for j in self.store.snapshot() if j["status"] == "pending" and j["network"] == network and j.get("not_before", 0) <= now]:
                if in_flight >= slots or self.blocked_until[network] > self.clock():
                    break
                in_flight += 1
                await self.submit(job, settings)

    def _block(self, network: str, seconds: float, reason: str) -> None:
        self.blocked_until[network] = self.clock() + seconds
        self.blocked_reason[network] = reason

    async def submit(self, job: dict[str, Any], settings: dict[str, Any]) -> None:
        job_id, network = job["id"], job["network"]
        self.store.update(job_id, status="submitting", attempts=job.get("attempts", 0) + 1, message="", waiting="")
        try:
            params = project_params({**job, "billing_mode": job.get("billing_mode") or settings["billing_mode"]})
            project_id, urls = await self.gateway.submit(params)
        except ValueError as error:  # parameter problems from the model mapping or SDK validation
            self.store.update(job_id, status="failed", message=str(error))
            return
        except GatewayError as error:
            self._handle_error(job_id, network, error, settings, submitted=False)
            return
        self.full_strikes[network] = 0
        self.store.update(job_id, status="active", project_id=project_id, submitted_at=self.clock())
        if urls:
            await self.finish(job_id, urls[0])
        else:
            self.monitors[job_id] = asyncio.ensure_future(self.monitor(job_id, project_id, live=True))

    def _handle_error(self, job_id: str, network: str, error: GatewayError, settings: dict[str, Any], *, submitted: bool) -> None:
        code = _int_code(error.code)
        job = self.store.get(job_id) or {}
        if code == QUEUE_FULL:
            strike = self.full_strikes[network]
            self.full_strikes[network] = strike + 1
            delay = error.retry_after or QUEUE_FULL_BACKOFF_S[min(strike, len(QUEUE_FULL_BACKOFF_S) - 1)]
            self._block(network, delay, "Sogni plan queue is full; waiting for a slot.")
            self.store.update(job_id, status="pending", project_id=None, waiting="Waiting for a free Sogni queue slot")
        elif code in FAST_CAPACITY and network == "fast":
            if settings.get("fallback_to_relaxed"):
                self.store.update(job_id, status="pending", project_id=None, network="relaxed",
                                  waiting="Fast capacity used up; moved to Relaxed")
                for other in self.store.snapshot():
                    if other["status"] == "pending" and other["network"] == "fast":
                        self.store.update(other["id"], network="relaxed", waiting="Fast capacity used up; moved to Relaxed")
            else:
                self._block("fast", 1800, "Your plan's Fast capacity is used up. Switch jobs to Relaxed or wait for the reset.")
                self.store.update(job_id, status="pending", project_id=None, waiting="Fast capacity used up")
        elif code == RENEWAL_RETRY:
            for name in NETWORKS:
                self._block(name, 900, "Sogni is retrying your plan renewal; Unlimited renders are paused.")
            self.store.update(job_id, status="pending", project_id=None, waiting="Plan renewal pending")
        elif error.transient and job.get("attempts", 0) < MAX_SUBMIT_ATTEMPTS:
            delay = error.retry_after or 15 * job.get("attempts", 1)
            self.store.update(job_id, status="pending", project_id=None, not_before=self.clock() + delay,
                              waiting=f"Connection problem; retrying in {int(delay)} s")
        else:
            self.store.update(job_id, status="failed", message=failure_text(error.message, code))

    async def monitor(self, job_id: str, project_id: str, *, live: bool) -> None:
        try:
            while True:
                job = self.store.get(job_id)
                if job is None or job["status"] not in ACTIVE:
                    return
                if self.gateway is None:
                    return
                try:
                    urls = await self.gateway.wait(project_id, LIVE_WAIT_S if live else RECOVERED_POLL_S)
                    if urls:
                        await self.finish(job_id, urls[0])
                        return
                    result = await self.gateway.result(project_id)
                except GatewayError as error:
                    if _int_code(error.code) in {QUEUE_FULL, RENEWAL_RETRY, *FAST_CAPACITY}:
                        self._handle_error(job_id, job["network"], error, self.settings(), submitted=False)
                        self.poke()
                        return
                    if error.transient:
                        continue
                    self.store.update(job_id, status="failed", message=failure_text(error.message, error.code))
                    self.poke()
                    return
                if await self.apply_result(job_id, result):
                    return
        finally:
            self.monitors.pop(job_id, None)

    async def apply_result(self, job_id: str, result: dict[str, Any]) -> bool:
        status = result.get("status")
        waiting = result.get("waitingReason") or {}
        if not result.get("finished"):
            self.store.update(job_id, waiting=waiting.get("message") or {"processing": "Rendering"}.get(status, "Queued on Sogni"))
            return False
        jobs = result.get("jobs") or []
        url = next((item.get("url") for item in jobs if item.get("url")), None)
        if status == "completed" and url:
            await self.finish(job_id, url)
        elif status == "completed":
            reason = next((item.get("urlUnavailable") for item in jobs if item.get("urlUnavailable")), "")
            message = SENSITIVE_HINT if reason == "sensitiveContent" else f"Finished without a video link ({reason or 'unknown'})."
            self.store.update(job_id, status="failed", message=message)
        else:
            failure = result.get("failure") or {}
            reason = failure.get("message") or next((item.get("reason") for item in jobs if item.get("reason")), "")
            self.store.update(job_id, status="cancelled" if status == "canceled" else "failed",
                              message=failure_text(reason) if reason else f"Sogni reported the render as {status}.")
        self.poke()
        return True

    async def finish(self, job_id: str, url: str) -> None:
        target = self.output_root() / f"{job_id}.mp4"
        try:
            if not target.exists():
                await asyncio.to_thread(self.downloader, url, target)
        except Exception as error:  # noqa: BLE001
            self.store.update(job_id, status="completed", video_url=url, waiting="",
                              message=f"Rendered, but saving failed: {error}. The link works for 24 hours.")
        else:
            self.store.update(job_id, status="completed", video_url=url, waiting="", message="",
                              saved_name=f"h3_sogni/{target.name}")
        self.poke()

    async def cancel(self, job_id: str) -> dict[str, Any] | None:
        job = self.store.get(job_id)
        if job is None:
            return None
        if job["status"] in ACTIVE and job.get("project_id") and self.gateway is not None:
            try:
                await self.gateway.cancel(job["project_id"])
            except GatewayError:
                pass
        task = self.monitors.pop(job_id, None)
        if task:
            task.cancel()
        if job["status"] not in FINISHED:
            self.store.update(job_id, status="cancelled", waiting="")
        self.poke()
        return self.store.get(job_id)

    def retry(self, job_id: str) -> dict[str, Any] | None:
        job = self.store.get(job_id)
        if job is None or job["status"] not in {"failed", "cancelled"}:
            return job
        if job.get("media_count") and not self.store.job_dir(job_id).exists():
            self.store.update(job_id, message="Its reference media was cleaned up; send it again from Writer.")
            return job
        updated = self.store.update(job_id, status="pending", project_id=None, message="", waiting="",
                                    finished_at=None, attempts=0, not_before=0)
        self.poke()
        return updated

    def set_network(self, job_id: str, network: str) -> dict[str, Any] | None:
        job = self.store.get(job_id)
        if job is None or job["status"] != "pending" or network not in NETWORKS:
            return job
        updated = self.store.update(job_id, network=network, waiting="")
        self.poke()
        return updated

    def resume(self) -> None:
        for network in NETWORKS:
            self.blocked_until[network] = 0
            self.blocked_reason[network] = ""
            self.full_strikes[network] = 0
        self.poke()

    async def lora_catalog(self) -> dict[str, Any]:
        if not await self.ensure_gateway():
            raise sogni.SogniError(self.state, self.state_message, status=502)
        self.idle_since = None
        try:
            rows = await self.gateway.loras()
        except GatewayError as error:
            raise sogni.SogniError("SOGNI_LORAS", f"Could not load Sogni LoRAs: {error.message}", status=502) from None
        return {"loras": lora_rows(rows)}

    def overview(self) -> dict[str, Any]:
        now = self.clock()
        return {
            "state": self.state,
            "message": self.state_message,
            "sdk_installed": sdk_available(),
            "networks": {
                network: {
                    "blocked_seconds": max(0, int(self.blocked_until[network] - now)),
                    "reason": self.blocked_reason[network] if self.blocked_until[network] > now else "",
                } for network in NETWORKS
            },
        }


def lora_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Sogni catalog rows that work with at least one H3 model Writer sends, trimmed for the picker."""
    ours = all_model_ids()
    picked = []
    for row in rows:
        models = set(row.get("modelIds") or []) & ours
        if not row.get("loraId") or not models:
            continue
        ui = row.get("ui") or {}
        picked.append({
            "id": row["loraId"], "name": ui.get("label") or row.get("name") or row["loraId"],
            "description": str(row.get("description") or "")[:400],
            "personal": ui.get("category") == "personal", "nsfw": bool(ui.get("nsfw")),
            "min": ui.get("min", 0), "max": ui.get("max", 1), "step": ui.get("step", 0.05),
            "default": ui.get("default", 1),
            "recommended": [ui.get("recommendedMin"), ui.get("recommendedMax")],
            "warning": ui.get("strengthWarning"),
            "partial": len(models) < len(ours),
        })
    return sorted(picked, key=lambda item: (not item["personal"], item["name"].lower()))


def failure_text(message: str, code: Any = None) -> str:
    if "sensitive content" in message.lower():
        return SENSITIVE_HINT
    return f"{message} ({code})" if code else message


def _int_code(code: Any) -> int | None:
    try:
        return int(code)
    except (TypeError, ValueError):
        return None


STORE = JobStore(sogni.user_root() / "sogni_queue")
DISPATCHER = Dispatcher(STORE)
