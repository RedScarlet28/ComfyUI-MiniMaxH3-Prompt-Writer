// Dictate into the Creative brief: record in the browser, transcribe locally with Parakeet (backend/speech.py).
import { api } from "/scripts/api.js";
import { readApiResponse } from "./api/response.js";

const PREFIX = "/h3studio/speech";
const MAX_RECORDING_MS = 5 * 60 * 1000;
const MIC = '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5 11a7 7 0 0 0 14 0M12 18v3"/></svg>';

export function dictationButtonMarkup() {
  return `<button class="h3ps-library-chip h3ps-dictate" type="button" data-dictate title="Dictate into the brief (runs locally)">${MIC}<span data-dictate-label>Dictate</span></button>`;
}

// Text goes in at the caret through insertText so Ctrl+Z undoes it.
export function insertDictation(field, text) {
  const start = field.selectionStart ?? field.value.length;
  const end = field.selectionEnd ?? start;
  const before = field.value.slice(0, start);
  const spaced = (before && !/\s$/.test(before) ? " " : "") + text;
  field.focus();
  field.setSelectionRange(start, end);
  if (!field.ownerDocument.execCommand?.("insertText", false, spaced)) {
    field.setRangeText(spaced, start, end, "end");
    field.dispatchEvent(new field.ownerDocument.defaultView.Event("input", { bubbles: true }));
  }
}

export function bindDictation({ root, getField, onChanged, showToast }) {
  const button = root.querySelector("[data-dictate]");
  const label = button.querySelector("[data-dictate-label]");
  let recorder = null;
  let stopTimer = null;
  let caret = null;

  const setState = (state, text) => {
    button.dataset.state = state;
    button.setAttribute("aria-pressed", String(state === "recording"));
    button.disabled = state === "busy";
    label.textContent = text;
  };

  async function download(sizeMb) {
    setState("busy", "Downloading…");
    showToast("Downloading speech model", `Parakeet (about ${sizeMb} MB) is going into ComfyUI's models folder. This happens once.`, null, null, { durationMs: 600000 });
    try {
      await readApiResponse(await api.fetchApi(`${PREFIX}/download`, { method: "POST" }));
      showToast("Dictation ready", "Click Dictate, speak, then click Stop.");
    } catch (error) {
      showToast("Speech model not downloaded", error.message);
    } finally {
      setState("idle", "Dictate");
    }
  }

  async function start() {
    const status = await readApiResponse(await api.fetchApi(`${PREFIX}/status`));
    if (!status.installed) {
      showToast("Dictation needs onnx-asr", "Run: python -m pip install onnx-asr[cpu] in ComfyUI's environment, then restart ComfyUI.");
      return;
    }
    if (!status.downloaded) {
      showToast("Download the speech model?", `Dictation runs locally with ${status.model}. It needs a one-time download of about ${status.download_mb} MB.`, null,
        { label: "Download", onClick: () => download(status.download_mb) });
      return;
    }
    const field = getField();
    caret = [field.selectionStart, field.selectionEnd];
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    const chunks = [];
    recorder = new MediaRecorder(stream);
    recorder.addEventListener("dataavailable", (event) => event.data.size && chunks.push(event.data));
    recorder.addEventListener("stop", () => {
      stream.getTracks().forEach((track) => track.stop());
      transcribe(new Blob(chunks, { type: recorder.mimeType }));
      recorder = null;
    });
    recorder.start();
    stopTimer = setTimeout(stop, MAX_RECORDING_MS);
    setState("recording", "Stop");
  }

  function stop() {
    clearTimeout(stopTimer);
    if (recorder?.state === "recording") recorder.stop();
  }

  async function transcribe(blob) {
    setState("busy", "Transcribing…");
    try {
      const { text } = await readApiResponse(await api.fetchApi(`${PREFIX}/transcribe`, { method: "POST", headers: { "Content-Type": blob.type || "audio/webm" }, body: blob }));
      if (!text) {
        showToast("Nothing heard", "No speech was picked up. Check the microphone and try again.");
        return;
      }
      const field = getField();
      field.setSelectionRange(...caret);
      insertDictation(field, text);
      onChanged();
    } catch (error) {
      showToast("Dictation failed", error.message);
    } finally {
      setState("idle", "Dictate");
    }
  }

  button.addEventListener("click", () => {
    if (recorder) return stop();
    start().catch((error) => {
      setState("idle", "Dictate");
      const blocked = error.name === "NotAllowedError" || error.name === "NotFoundError";
      showToast("Dictation unavailable", blocked ? "Allow microphone access for ComfyUI in the browser, or connect a microphone." : error.message);
    });
  });
  setState("idle", "Dictate");
}
