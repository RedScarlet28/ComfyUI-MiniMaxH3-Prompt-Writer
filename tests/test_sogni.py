import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image

from backend import sogni, sogni_models as models
from backend.sogni_queue import SENSITIVE_HINT, Dispatcher, GatewayError, JobStore, lora_rows


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "sogni.json"
        env = mock.patch.dict(os.environ, {"SOGNI_API_KEY": ""})
        env.start()
        self.addCleanup(env.stop)

    def tearDown(self):
        self.temp.cleanup()

    def test_key_is_hidden_and_defaults_are_safe(self):
        public = sogni.save_settings({"api_key": "sk-test-123456789", "fast_slots": 99, "network": "moon"}, self.path)
        self.assertNotIn("api_key", public)
        self.assertEqual(public["key_hint"], "…6789")
        self.assertEqual(public["billing_mode"], "subscription")
        self.assertEqual(public["network"], "fast")
        self.assertEqual(public["fast_slots"], sogni.MAX_SLOTS)
        self.assertFalse(sogni.save_settings({"api_key": ""}, self.path)["key_configured"])

    def test_app_id_is_stable(self):
        first = sogni.app_id(self.path)
        self.assertEqual(first, sogni.app_id(self.path))
        self.assertTrue(first.startswith("h3-prompt-writer-"))

    def test_environment_key(self):
        with mock.patch.dict(os.environ, {"SOGNI_API_KEY": "env-key-abcdefgh"}):
            self.assertEqual(sogni.public_settings(self.path)["key_source"], "environment")


class ModelTests(unittest.TestCase):
    def test_canvas_sizes_follow_h3_grid(self):
        self.assertEqual(models.canvas_size(16 / 9, "768p"), (1344, 768))
        self.assertEqual(models.canvas_size(9 / 16, "768p"), (768, 1344))
        self.assertEqual(models.canvas_size(16 / 9, "480p"), (832, 480))
        self.assertEqual(models.canvas_size(16 / 9, "1080p"), (960, 544))
        for ratio in ("1:1", "2:3", "3:2", "3:4", "4:3", "21:9"):
            for resolution in models.RESOLUTIONS:
                width, height = models.canvas_size(models.parse_ratio(ratio), resolution)
                self.assertEqual((width % 32, height % 32), (0, 0))
                self.assertLessEqual(max(width, height), 1344)
                self.assertLessEqual(width * height, 1_032_192)

    def test_model_ids(self):
        self.assertEqual(models.model_id("T2VA", "fasth3", "768p"), "minimax-h3-fastvideo-int8_t2v_turbo")
        self.assertEqual(models.model_id("L2VA", "standard", "768p"), "minimax-h3-fl2va-fp8_i2v")
        self.assertEqual(models.model_id("Reference", "balanced", "2k"), "minimax-h3-ref2va-fp8_r2v_balanced_2stage")
        with self.assertRaises(models.SogniOptionError):
            models.model_id("T2VA", "turbo", "1080p")
        with self.assertRaises(models.SogniOptionError):
            models.model_id("Music3", "fasth3", "768p")

    def test_reference_media_keeps_writer_order(self):
        media = [
            {"type": "image", "path": "p1.png"}, {"type": "audio", "path": "a1.wav"},
            {"type": "image", "path": "p2.png"}, {"type": "video", "path": "v1.mp4"},
            {"type": "image", "path": "p3.png"}, {"type": "video", "path": "v2.mp4"},
        ]
        fields = models.assign_media("Reference", media)
        self.assertEqual(fields["referenceImage"], "p1.png")
        self.assertEqual(fields["contextImages"], ["p2.png", "p3.png"])
        self.assertEqual(fields["referenceVideo"], "v1.mp4")
        self.assertEqual(fields["referenceVideos"], ["v2.mp4"])
        self.assertEqual(fields["referenceAudio"], "a1.wav")
        self.assertNotIn("referenceAudios", fields)

    def test_frame_modes(self):
        first, last = {"type": "image", "path": "a.png"}, {"type": "image", "path": "b.png"}
        self.assertEqual(models.assign_media("FL2VA", [first, last]), {"referenceImage": "a.png", "referenceImageEnd": "b.png"})
        self.assertEqual(models.assign_media("L2VA", [last]), {"referenceImageEnd": "b.png"})
        with self.assertRaises(models.SogniOptionError):
            models.assign_media("I2VA", [])
        with self.assertRaises(models.SogniOptionError):
            models.assign_media("Reference", [{"type": "audio", "path": "a.wav"}])
        self.assertAlmostEqual(models.frame_ratio("I2VA", [{"type": "image", "width": 900, "height": 1600}], 16 / 9), 0.5625)
        self.assertAlmostEqual(models.frame_ratio("T2VA", [], 4 / 3), 4 / 3)

    def test_project_params(self):
        params = models.project_params({
            "model_id": "m", "prompt": "p", "network": "relaxed", "width": 1344, "height": 768,
            "duration": 8, "billing_mode": "subscription", "mode": "T2VA", "media": [],
        })
        self.assertEqual(params["network"], "relaxed")
        self.assertEqual(params["billingMode"], "subscription")
        self.assertEqual((params["width"], params["height"], params["duration"]), (1344, 768, 8))
        self.assertEqual(models.clamp_duration(20), 15)
        self.assertEqual(models.clamp_duration(1), 5)
        self.assertNotIn("loras", params)
        self.assertNotIn("disableNSFWFilter", params)

    def test_project_params_loras_and_filter(self):
        params = models.project_params({
            "model_id": "m", "prompt": "p", "network": "fast", "width": 1344, "height": 768, "duration": 6,
            "billing_mode": "subscription", "mode": "T2VA", "media": [], "content_filter": False,
            "loras": [{"id": "h3-better-motion", "strength": 0.6}, {"id": "personal-x", "strength": 0.8}],
        })
        self.assertEqual(params["loras"], ["h3-better-motion", "personal-x"])
        self.assertEqual(params["loraStrengths"], [0.6, 0.8])
        self.assertTrue(params["disableNSFWFilter"])

    def test_lora_stack_is_normalized(self):
        raw = [{"id": "a", "strength": 500}, {"id": "a", "strength": 1}, {"id": "", "strength": 1}, {"id": "b", "strength": "x"}]
        raw += [{"id": f"l{i}", "strength": 0.5} for i in range(10)]
        stack = sogni.normalize({"loras": raw})["loras"]
        self.assertEqual(stack[0], {"id": "a", "name": "a", "strength": 100.0})
        self.assertEqual(len(stack), sogni.MAX_LORAS)
        self.assertTrue(sogni.normalize({})["content_filter"])

    def test_lora_rows_keep_h3_loras(self):
        rows = [
            {"loraId": "h3-a", "name": "A", "modelIds": sorted(models.all_model_ids()), "ui": {"default": 0.8, "nsfw": True}},
            {"loraId": "p", "name": "Mine", "modelIds": ["minimax-h3-fl2va-fp8_t2v"], "ui": {"category": "personal", "label": "Mine"}},
            {"loraId": "flux", "name": "Flux", "modelIds": ["flux1-dev"]},
        ]
        picked = lora_rows(rows)
        self.assertEqual([row["id"] for row in picked], ["p", "h3-a"])
        self.assertTrue(picked[0]["personal"] and picked[0]["partial"])
        self.assertFalse(picked[1]["partial"])
        self.assertEqual(picked[1]["default"], 0.8)


class FakeGateway:
    def __init__(self):
        self.submitted = []
        self.errors = []
        self.results = {}
        self.cancelled = []
        self.counter = 0

    async def open(self):
        pass

    async def close(self):
        pass

    async def submit(self, params):
        self.submitted.append(params)
        if self.errors:
            raise self.errors.pop(0)
        self.counter += 1
        return f"proj{self.counter}", None

    async def wait(self, project_id, timeout):
        await asyncio.sleep(0)
        return None

    async def result(self, project_id):
        return self.results.get(project_id, {"status": "processing", "finished": False, "jobs": []})

    async def cancel(self, project_id):
        self.cancelled.append(project_id)


class QueueTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = JobStore(self.root / "queue")
        self.gateway = FakeGateway()
        self.now = [1000.0]
        self.settings = {"fast_slots": 2, "relaxed_slots": 3, "billing_mode": "subscription", "fallback_to_relaxed": False}
        self.saved = []
        self.dispatcher = Dispatcher(
            self.store, gateway_factory=lambda: self.gateway, settings=lambda: self.settings,
            downloader=lambda url, target: (target.parent.mkdir(parents=True, exist_ok=True), target.write_bytes(b"mp4"), self.saved.append(url)),
            output_root=lambda: self.root / "out", clock=lambda: self.now[0],
        )
        self.dispatcher.wake = mock.Mock()
        self.dispatcher.poke = lambda: None

    def tearDown(self):
        self.temp.cleanup()

    def add(self, network="fast", media=()):
        job = {"title": "t", "prompt": "p", "mode": "T2VA" if not media else "I2VA", "network": network, "tier": "fasth3",
               "resolution": "768p", "model_id": "m", "width": 1344, "height": 768, "duration": 5, "billing_mode": "subscription"}
        return self.store.add(job, list(media))

    def run_tick(self):
        async def go():
            await self.dispatcher.tick()
            await asyncio.sleep(0)
        asyncio.run(go())

    def statuses(self):
        return [job["status"] for job in self.store.snapshot()]

    def test_fills_slots_per_network(self):
        for _ in range(4):
            self.add("fast")
        for _ in range(4):
            self.add("relaxed")
        self.run_tick()
        jobs = self.store.snapshot()
        self.assertEqual(sum(1 for j in jobs if j["network"] == "fast" and j["status"] == "active"), 2)
        self.assertEqual(sum(1 for j in jobs if j["network"] == "relaxed" and j["status"] == "active"), 3)
        self.assertEqual(self.gateway.submitted[0]["network"], "fast")

    def test_queue_full_backs_off_and_retries_later(self):
        self.add("relaxed")
        self.add("relaxed")
        self.gateway.errors = [GatewayError(4079, "Maximum queued jobs reached")]
        self.run_tick()
        self.assertEqual(self.statuses(), ["pending", "pending"])
        self.assertIn("free Sogni queue slot", self.store.snapshot()[0]["waiting"])
        self.run_tick()  # still blocked
        self.assertEqual(len(self.gateway.submitted), 1)
        self.now[0] += 31
        self.run_tick()
        self.assertEqual(self.statuses(), ["active", "active"])

    def test_fast_capacity_can_fall_back_to_relaxed(self):
        self.settings["fallback_to_relaxed"] = True
        self.add("fast")
        self.add("fast")
        self.gateway.errors = [GatewayError(4087, "Daily Fast capacity used")]
        self.run_tick()
        networks = [job["network"] for job in self.store.snapshot()]
        self.assertEqual(networks, ["relaxed", "relaxed"])

    def test_fast_capacity_without_fallback_pauses_fast_only(self):
        self.add("fast")
        self.add("relaxed")
        self.gateway.errors = [GatewayError(4089, "Monthly Fast capacity used")]
        self.run_tick()
        self.assertEqual(self.statuses(), ["pending", "active"])
        self.assertGreater(self.dispatcher.overview()["networks"]["fast"]["blocked_seconds"], 0)

    def test_hard_error_fails_job(self):
        self.add()
        self.gateway.errors = [GatewayError(4101, "Bad size")]
        self.run_tick()
        job = self.store.snapshot()[0]
        self.assertEqual(job["status"], "failed")
        self.assertIn("4101", job["message"])

    def test_completed_result_downloads(self):
        record = self.add()
        self.run_tick()
        self.dispatcher.on_saved = mock.Mock()
        self.gateway.results["proj1"] = {"status": "completed", "finished": True, "jobs": [{"url": "https://cdn/v.mp4"}]}
        asyncio.run(self.dispatcher.apply_result(record["id"], self.gateway.results["proj1"]))
        job = self.store.get(record["id"])
        self.assertEqual(job["status"], "completed")
        self.dispatcher.on_saved.assert_called_once_with(job)
        self.assertEqual(job["saved_name"], f"h3_sogni/{record['id']}.mp4")
        self.assertEqual(self.saved, ["https://cdn/v.mp4"])

    def test_failed_result_and_waiting_reason(self):
        record = self.add()
        self.run_tick()
        asyncio.run(self.dispatcher.apply_result(record["id"], {"status": "queued", "finished": False, "waitingReason": {"message": "Waiting for a worker"}}))
        self.assertEqual(self.store.get(record["id"])["waiting"], "Waiting for a worker")
        asyncio.run(self.dispatcher.apply_result(record["id"], {"status": "failed", "finished": True, "failure": {"message": "genfailure"}}))
        self.assertEqual(self.store.get(record["id"])["status"], "failed")

    def test_sensitive_content_points_to_filter_setting(self):
        record = self.add()
        self.gateway.errors = [GatewayError(5000, "Sensitive content detected.")]
        self.run_tick()
        self.assertEqual(self.store.get(record["id"])["message"], SENSITIVE_HINT)

    def test_worker_failure_is_resubmitted_once(self):
        record = self.add()
        failure = GatewayError(5000, "A worker couldn't complete this generation. Please try again.")
        self.gateway.errors = [failure]
        self.run_tick()
        job = self.store.get(record["id"])
        self.assertEqual((job["status"], job["worker_retried"]), ("pending", True))
        self.gateway.errors = [failure]
        self.run_tick()
        job = self.store.get(record["id"])
        self.assertEqual(job["status"], "failed")
        self.assertIn("(5000)", job["message"])
        self.assertFalse(self.dispatcher.retry(record["id"])["worker_retried"])

    def test_media_is_copied_and_kept_for_retry(self):
        source = self.root / "pic.png"
        source.write_bytes(b"png")
        record = self.add(media=[{"type": "image", "path": str(source), "width": 10, "height": 10}])
        copied = Path(record["media"][0]["path"])
        self.assertTrue(copied.exists())
        self.assertNotEqual(copied, source)
        self.store.update(record["id"], status="failed")
        self.assertTrue(copied.exists())
        self.assertEqual(self.dispatcher.retry(record["id"])["status"], "pending")
        self.store.update(record["id"], status="completed")
        self.assertFalse(copied.exists())

    def test_reference_video_over_15s_is_trimmed(self):
        source = self.root / "clip.mov"
        source.write_bytes(b"mov")
        with mock.patch("backend.sogni_queue.render_video") as render:
            record = self.add(media=[{"type": "video", "path": str(source), "width": 640, "height": 360, "duration": 15.04}])
        target, edit = Path(render.call_args[0][1]), render.call_args[0][2]
        self.assertEqual(record["media"][0]["path"], str(target))
        self.assertEqual(target.suffix, ".mp4")
        self.assertEqual(edit, {"crop": {"x": 0, "y": 0, "w": 640, "h": 360}, "start": 0, "end": 14.9})

    def test_restart_recovers_submitting_jobs(self):
        record = self.add()
        self.store.update(record["id"], status="submitting")
        reloaded = JobStore(self.root / "queue")
        self.assertEqual(reloaded.get(record["id"])["status"], "pending")

    def test_cancel_pending_and_active(self):
        pending = self.add("fast")
        asyncio.run(self.dispatcher.cancel(pending["id"]))
        self.assertEqual(self.store.get(pending["id"])["status"], "cancelled")
        active = self.add("fast")

        async def go():
            await self.dispatcher.tick()
            await self.dispatcher.cancel(active["id"])
        asyncio.run(go())
        self.assertEqual(self.store.get(active["id"])["status"], "cancelled")
        self.assertEqual(self.gateway.cancelled, ["proj1"])

    def test_remove_refuses_active_job(self):
        record = self.add()
        self.run_tick()
        self.assertFalse(self.store.remove(record["id"]))
        self.store.update(record["id"], status="failed")
        self.assertTrue(self.store.remove(record["id"]))

    def test_waiting_jobs_can_be_moved_and_are_sent_in_the_new_order(self):
        self.settings["fast_slots"] = 1
        first, second, third = self.add(), self.add(), self.add()
        self.assertTrue(self.store.move(third["id"], first["id"]))
        self.assertTrue(self.store.move(first["id"], None))
        self.assertEqual([job["id"] for job in self.store.snapshot()], [third["id"], second["id"], first["id"]])
        self.assertFalse(self.store.move(first["id"], "missing"))
        self.run_tick()
        self.assertEqual(self.store.get(third["id"])["status"], "active")
        self.assertFalse(self.store.move(third["id"], None))
        self.assertEqual([job["id"] for job in JobStore(self.root / "queue").snapshot()], [third["id"], second["id"], first["id"]])

    def test_thumbnail_is_made_from_the_first_picture_and_removed_with_the_job(self):
        source = self.root / "pic.png"
        Image.new("RGB", (800, 400), "teal").save(source)
        record = self.add(media=[{"type": "image", "path": str(source), "width": 800, "height": 400}])
        thumb = self.store.thumb_path(record["id"])
        with Image.open(thumb) as image:
            self.assertEqual(image.size, (160, 80))
        self.store.update(record["id"], status="completed")
        self.assertEqual(self.store.thumbnail(record["id"]), thumb)
        self.store.remove(record["id"])
        self.assertFalse(thumb.exists())
        self.assertIsNone(self.store.thumbnail(self.add()["id"]))


if __name__ == "__main__":
    unittest.main()
