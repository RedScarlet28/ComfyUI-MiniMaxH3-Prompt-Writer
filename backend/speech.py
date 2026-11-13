"""Local dictation for the Creative brief: NVIDIA Parakeet TDT 0.6B v3 (int8 ONNX) on the CPU via onnx-asr."""
from __future__ import annotations

import asyncio
import io
import shutil
import threading
from pathlib import Path
from typing import Any

import av
import folder_paths
import numpy as np
from aiohttp import web

MODEL_NAME = "nemo-parakeet-tdt-0.6b-v3"
MODEL_FILES = ("config.json", "encoder-model.int8.onnx", "decoder_joint-model.int8.onnx", "vocab.txt")
DOWNLOAD_MB = 640
MAX_AUDIO_BYTES = 25 * 1024 * 1024
SAMPLE_RATE = 16_000

_lock = threading.Lock()
_model = None


class SpeechError(Exception):
    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def model_dir() -> Path:
    return Path(folder_paths.models_dir) / "speech_to_text" / "parakeet-tdt-0.6b-v3-int8"


def runtime_installed() -> bool:
    try:
        import onnx_asr  # noqa: F401
    except ImportError:
        return False
    return True


def downloaded() -> bool:
    return all((model_dir() / name).is_file() for name in MODEL_FILES)


def status() -> dict[str, Any]:
    return {"installed": runtime_installed(), "downloaded": downloaded(), "download_mb": DOWNLOAD_MB, "model": "Parakeet TDT 0.6B v3"}


def download() -> None:
    """Fetch the int8 model into a .part folder first so an interrupted download is never mistaken for a finished one."""
    if not runtime_installed():
        raise SpeechError("SPEECH_NOT_INSTALLED", "Run: python -m pip install onnx-asr[cpu] in ComfyUI's environment, then restart ComfyUI.")
    import onnx_asr

    with _lock:
        if downloaded():
            return
        target = model_dir()
        part = target.with_name(target.name + ".part")
        shutil.rmtree(part, ignore_errors=True)
        try:
            onnx_asr.load_model(MODEL_NAME, part, quantization="int8")
        except Exception as error:  # huggingface_hub and onnxruntime raise many unrelated types
            shutil.rmtree(part, ignore_errors=True)
            raise SpeechError("SPEECH_DOWNLOAD_FAILED", f"Could not download the speech model: {error}", status=502) from None
        shutil.rmtree(target, ignore_errors=True)
        part.rename(target)


def decode(data: bytes) -> np.ndarray:
    """Any browser recording (webm/opus, ogg, mp4, wav) to 16 kHz mono float32."""
    resampler = av.AudioResampler(format="flt", layout="mono", rate=SAMPLE_RATE)
    chunks = []
    try:
        with av.open(io.BytesIO(data)) as container:
            for frame in container.decode(audio=0):
                chunks.extend(out.to_ndarray().reshape(-1) for out in resampler.resample(frame))
            chunks.extend(out.to_ndarray().reshape(-1) for out in resampler.resample(None))
    except (av.error.FFmpegError, IndexError) as error:
        raise SpeechError("SPEECH_BAD_AUDIO", f"Could not read the recording: {error}") from None
    return np.concatenate(chunks) if chunks else np.zeros(0, dtype=np.float32)


def transcribe(data: bytes) -> str:
    global _model
    if not downloaded():
        raise SpeechError("SPEECH_NOT_DOWNLOADED", "Download the speech model first.", status=409)
    waveform = decode(data)
    if waveform.size < SAMPLE_RATE // 4:
        return ""
    import onnx_asr

    with _lock:
        if _model is None:
            _model = onnx_asr.load_model(MODEL_NAME, model_dir(), quantization="int8", providers=["CPUExecutionProvider"])
        return _model.recognize(waveform, sample_rate=SAMPLE_RATE).strip()


def register_speech_routes(routes, prefix: str) -> None:
    base = f"{prefix}/speech"

    def failure(error: SpeechError) -> web.Response:
        return web.json_response({"error": {"code": error.code, "message": error.message}}, status=error.status)

    @routes.get(f"{base}/status")
    async def get_speech_status(_request):
        return web.json_response(status())

    @routes.post(f"{base}/download")
    async def post_speech_download(_request):
        try:
            await asyncio.to_thread(download)
        except SpeechError as error:
            return failure(error)
        return web.json_response(status())

    @routes.post(f"{base}/transcribe")
    async def post_speech_transcribe(request):
        data = await request.read()
        if not data or len(data) > MAX_AUDIO_BYTES:
            return failure(SpeechError("SPEECH_BAD_AUDIO", "The recording is empty or larger than 25 MB."))
        try:
            text = await asyncio.to_thread(transcribe, data)
        except SpeechError as error:
            return failure(error)
        return web.json_response({"text": text})
