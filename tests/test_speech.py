import io
import tempfile
import unittest
import wave
from pathlib import Path
from unittest import mock

import numpy as np

from backend import speech


def wav_bytes(seconds, rate=44_100, channels=2):
    samples = (np.sin(np.linspace(0, 440 * 2 * np.pi * seconds, int(rate * seconds))) * 12_000).astype(np.int16)
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as out:
        out.setnchannels(channels)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(np.repeat(samples, channels).tobytes())
    return buffer.getvalue()


class SpeechTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        patcher = mock.patch.object(speech.folder_paths, "models_dir", self.temp.name, create=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.temp.cleanup)

    def test_recordings_become_16khz_mono(self):
        waveform = speech.decode(wav_bytes(2.0))
        self.assertEqual(waveform.dtype, np.float32)
        self.assertAlmostEqual(waveform.size / speech.SAMPLE_RATE, 2.0, delta=0.05)
        with self.assertRaises(speech.SpeechError):
            speech.decode(b"not audio")

    def test_model_must_be_downloaded_before_transcribing(self):
        self.assertFalse(speech.status()["downloaded"])
        with self.assertRaises(speech.SpeechError) as raised:
            speech.transcribe(wav_bytes(1.0))
        self.assertEqual(raised.exception.code, "SPEECH_NOT_DOWNLOADED")
        for name in speech.MODEL_FILES:
            path = speech.model_dir() / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"x")
        self.assertTrue(speech.status()["downloaded"])
        self.assertEqual(speech.transcribe(wav_bytes(0.1)), "")

    @unittest.skipUnless(speech.runtime_installed(), "onnx-asr is not installed")
    def test_failed_download_leaves_nothing_behind(self):
        def fail(_name, path, **_kwargs):
            Path(path, "encoder-model.int8.onnx").parent.mkdir(parents=True)
            Path(path, "encoder-model.int8.onnx").write_bytes(b"partial")
            raise OSError("connection reset")
        with mock.patch("onnx_asr.load_model", fail), self.assertRaises(speech.SpeechError):
            speech.download()
        self.assertEqual(list(Path(self.temp.name).rglob("*.onnx")), [])


if __name__ == "__main__":
    unittest.main()
