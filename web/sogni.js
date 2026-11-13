// Send to Sogni: a local render queue that feeds Sogni (Fast or Relaxed) as plan slots free up.
import { api } from "/scripts/api.js";
import { readApiResponse } from "./api/response.js";

const PREFIX = "/h3studio/sogni";
const POLL_ACTIVE_MS = 5000;
const POLL_IDLE_MS = 30000;
const UNFINISHED = new Set(["pending", "submitting", "active"]);
const VIDEO_MODES = new Set(["T2VA", "I2VA", "FL2VA", "L2VA", "Reference"]);

async function call(path, options) {
  return readApiResponse(await api.fetchApi(`${PREFIX}${path}`, options));
}
const post = (path, body = {}) => call(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[ch]);
const svg = (paths, size) => `<svg width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${paths}</svg>`;
const ICONS = {
  send: '<path d="M4 12a8 8 0 1 0 16 0 8 8 0 0 0-16 0Z"/><path d="m10 8.5 5 3.5-5 3.5v-7Z" fill="currentColor" stroke="none"/>',
  gear: '<circle cx="12" cy="12" r="3"/><path d="M12 2v3m0 14v3M4.9 4.9l2.1 2.1m10 10 2.1 2.1M2 12h3m14 0h3M4.9 19.1 7 17m10-10 2.1-2.1"/>',
  list: '<path d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01"/>',
};
const STATUS_LABEL = { pending: "Waiting", submitting: "Sending", active: "On Sogni", completed: "Done", failed: "Failed", cancelled: "Cancelled" };
const NETWORK_LABEL = { fast: "Fast", relaxed: "Relaxed" };

export function sogniActionsMarkup() {
  return `<span class="h3ps-sogni-actions" data-sogni-actions>
    <button class="h3ps-secondary-button h3ps-sogni-send" type="button" data-sogni-send title="Queue this prompt for rendering on Sogni">${svg(ICONS.send, 15)} <span>Send to Sogni</span></button>
    <span class="h3ps-sogni-network" role="group" aria-label="Sogni network">
      <button type="button" data-sogni-network="fast" aria-pressed="true" title="Fast network: quicker, uses your plan's Fast capacity">Fast</button>
      <button type="button" data-sogni-network="relaxed" aria-pressed="false" title="Relaxed network: included without limits, can take hours">Relaxed</button>
    </span>
    <button class="h3ps-icon-button h3ps-sogni-queue-toggle" type="button" data-sogni-queue-toggle title="Sogni render queue" aria-label="Sogni render queue" aria-expanded="false">${svg(ICONS.list, 14)}<b data-sogni-count hidden></b></button>
    <button class="h3ps-icon-button h3ps-sogni-lora-toggle" type="button" data-sogni-lora-toggle title="LoRAs sent with every Sogni render" aria-label="Sogni LoRAs" aria-expanded="false">LoRA<b data-sogni-lora-count hidden></b></button>
    <button class="h3ps-icon-button" type="button" data-sogni-settings-toggle title="Send to Sogni settings" aria-label="Send to Sogni settings" aria-expanded="false">${svg(ICONS.gear, 14)}</button>
    <div class="h3ps-sogni-panel" data-sogni-settings hidden role="dialog" aria-label="Send to Sogni settings"></div>
    <div class="h3ps-sogni-panel h3ps-sogni-loras" data-sogni-loras hidden role="dialog" aria-label="Sogni LoRAs"></div>
    <div class="h3ps-sogni-panel h3ps-sogni-queue" data-sogni-queue hidden role="dialog" aria-label="Sogni render queue"></div>
  </span>`;
}

function options(list, selected) {
  return list.map(({ id, label }) => `<option value="${esc(id)}" ${id === selected ? "selected" : ""}>${esc(label)}</option>`).join("");
}

function settingsMarkup(s) {
  const billingLabels = { subscription: "Unlimited plan only (never spend tokens)", auto: "Plan first, then tokens", tokens: "Always tokens (priority)" };
  const keyNote = s.key_source === "environment" ? "Using SOGNI_API_KEY from the environment." : s.key_configured ? `Saved key ${esc(s.key_hint)}` : "No key yet. Get yours at sogni.ai/keys.";
  const check = (key, label, hint) => `<label class="h3ps-sogni-check"><input type="checkbox" data-sogni-field="${key}" ${s[key] ? "checked" : ""}><span>${label}<small>${hint}</small></span></label>`;
  return `<strong>Send to Sogni</strong>
    <label>API key<input type="password" autocomplete="off" spellcheck="false" data-sogni-key placeholder="${s.key_configured ? "Leave blank to keep the saved key" : "Paste your Sogni API key"}"><small>${keyNote}</small></label>
    <div class="h3ps-sogni-grid">
      <label>Quality tier<select data-sogni-field="tier">${options(s.tiers, s.tier)}</select></label>
      <label>Resolution<select data-sogni-field="resolution">${options(s.resolutions, s.resolution)}</select></label>
      <label>Fast slots<input type="number" min="1" max="${s.max_slots}" value="${s.fast_slots}" data-sogni-field="fast_slots"></label>
      <label>Relaxed slots<input type="number" min="1" max="${s.max_slots}" value="${s.relaxed_slots}" data-sogni-field="relaxed_slots"></label>
    </div>
    <label>Billing<select data-sogni-field="billing_mode">${options(s.billing_modes.map((id) => ({ id, label: billingLabels[id] || id })), s.billing_mode)}</select></label>
    ${check("auto_send_queue", "Auto-send queued prompts", "When Writer's prompt queue finishes a tab, send it to Sogni.")}
    ${check("fallback_to_relaxed", "Move to Relaxed when Fast runs out", "If your daily or monthly Fast capacity is used up, waiting jobs switch to Relaxed.")}
    ${check("content_filter", "Sensitive Content Filter", "When on, Sogni withholds any render it flags as sensitive and the job fails. Turn it off to receive those videos.")}
    <small class="h3ps-sogni-note">Slots are how many jobs Writer keeps on Sogni per network. When Sogni's queue is full, Writer waits and sends the next job as soon as one finishes. Length and aspect ratio follow Writer's Duration and Aspect ratio; image modes follow the image's shape. 2-stage sizes need FastH3, or Standard/Balanced in Reference.</small>
    <div class="h3ps-sogni-panel-actions">
      ${s.key_source === "settings" ? '<button type="button" class="h3ps-text-button" data-sogni-forget>Remove key</button>' : "<span></span>"}
      <button type="button" class="h3ps-secondary-button" data-sogni-save>Save</button>
    </div>`;
}

function jobMeta(job) {
  return [job.mode, NETWORK_LABEL[job.network] || job.network, job.resolution, `${job.width}×${job.height}`, `${job.duration}s`,
    job.media_count ? `${job.media_count} ref${job.media_count === 1 ? "" : "s"}` : "",
    job.loras?.length ? `${job.loras.length} LoRA${job.loras.length === 1 ? "" : "s"}` : ""].filter(Boolean).join(" · ");
}

function loraRow(lora, picked) {
  const on = Boolean(picked);
  const strength = on ? picked.strength : lora.default;
  const [lo, hi] = lora.recommended;
  const tags = [lora.personal ? "Yours" : "", lora.nsfw ? "18+" : "", lora.partial ? "Some tiers" : ""].filter(Boolean)
    .map((tag) => `<span class="h3ps-sogni-tag">${tag}</span>`).join("");
  const warning = lora.warning && strength > lora.warning.above ? ` Above ${lora.warning.above} often gives artifacts.` : "";
  return `<li class="h3ps-sogni-lora ${on ? "is-on" : ""}" data-lora-id="${esc(lora.id)}">
    <label title="${esc(lora.description)}"><input type="checkbox" data-lora-pick ${on ? "checked" : ""}><strong>${esc(lora.name)}</strong>${tags}</label>
    ${on ? `<div class="h3ps-sogni-lora-strength">
      <input type="range" min="${lora.min}" max="${lora.max}" step="${lora.step}" value="${strength}" data-lora-strength aria-label="${esc(lora.name)} strength">
      <output>${strength}</output>
    </div>
    <small>${lo != null && hi != null ? `Recommended ${lo}–${hi}.` : ""}${warning}</small>` : ""}
  </li>`;
}

function lorasMarkup(catalog, stack, max, filter) {
  const picked = new Map(stack.map((item) => [item.id, item]));
  const head = `<div class="h3ps-sogni-queue-head"><strong>LoRAs</strong><small>${stack.length}/${max} on · sent with every render</small></div>
    <input type="search" placeholder="Filter LoRAs" value="${esc(filter)}" data-lora-filter>`;
  if (catalog.error) return `${head}<p class="h3ps-sogni-banner is-error">${esc(catalog.error)}</p><button type="button" class="h3ps-text-button" data-lora-reload>Try again</button>`;
  if (!catalog.loras) return `${head}<p class="h3ps-sogni-empty">Loading Sogni's LoRAs…</p>`;
  const query = filter.trim().toLowerCase();
  const rows = catalog.loras.filter((lora) => picked.has(lora.id) || !query || lora.name.toLowerCase().includes(query));
  const missing = stack.filter((item) => !catalog.loras.some((lora) => lora.id === item.id));
  return `${head}
    ${missing.length ? `<p class="h3ps-sogni-banner">No longer on Sogni: ${missing.map((item) => esc(item.name)).join(", ")}. <button type="button" class="h3ps-text-button" data-lora-drop-missing>Remove</button></p>` : ""}
    <ul class="h3ps-sogni-lora-list">${rows.map((lora) => loraRow(lora, picked.get(lora.id))).join("") || '<li class="h3ps-sogni-empty">No match.</li>'}</ul>
    <div class="h3ps-sogni-queue-tools"><button type="button" class="h3ps-text-button" data-lora-reload>Refresh list</button>${stack.length ? '<button type="button" class="h3ps-text-button" data-lora-clear>Turn all off</button>' : ""}</div>`;
}

// Waiting jobs of one network are sent oldest first, so their order in state.jobs is the line.
function lineOf(jobs, job) {
  return jobs.filter((item) => item.status === "pending" && item.network === job.network);
}

function jobRow(job, jobs) {
  const actions = [];
  const line = job.status === "pending" ? lineOf(jobs, job) : [];
  const place = line.indexOf(job);
  if (job.status === "completed" && (job.saved_name || job.video_url)) actions.push(`<button type="button" data-job-action="open">Open</button>`);
  if (job.status === "pending") actions.push(`<button type="button" data-job-action="network" data-network="${job.network === "fast" ? "relaxed" : "fast"}">→ ${job.network === "fast" ? "Relaxed" : "Fast"}</button>`);
  if (UNFINISHED.has(job.status)) actions.push(`<button type="button" data-job-action="cancel">Cancel</button>`);
  if (job.status === "failed" || job.status === "cancelled") actions.push(`<button type="button" data-job-action="retry">Retry</button>`);
  if (!UNFINISHED.has(job.status)) actions.push(`<button type="button" data-job-action="remove" title="Remove from list">✕</button>`);
  const note = job.message || job.waiting || "";
  const meta = place >= 0 ? `#${place + 1} in ${NETWORK_LABEL[job.network]} line · ${jobMeta(job)}` : jobMeta(job);
  const thumb = api.apiURL(`${PREFIX}/jobs/${encodeURIComponent(job.id)}/thumb?v=${job.status === "completed" ? 1 : 0}`);
  const drag = job.status === "pending" ? ' draggable="true" title="Drag to change when this renders"' : "";
  return `<li class="h3ps-sogni-job is-${esc(job.status)}" data-job-id="${esc(job.id)}"${drag}>
    <img class="h3ps-sogni-thumb" src="${esc(thumb)}" alt="" loading="lazy" draggable="false">
    <div><span class="h3ps-sogni-chip">${esc(STATUS_LABEL[job.status] || job.status)}</span><strong title="${esc(job.title)}">${esc(job.title)}</strong></div>
    <small>${esc(meta)}</small>
    ${note ? `<small class="h3ps-sogni-job-note">${esc(note)}</small>` : ""}
    <div class="h3ps-sogni-job-actions">${actions.join("")}</div>
  </li>`;
}

function queueMarkup(state) {
  const jobs = [...state.jobs].reverse();
  const counts = jobs.reduce((acc, job) => ({ ...acc, [job.status]: (acc[job.status] || 0) + 1 }), {});
  const blocked = Object.entries(state.networks || {}).filter(([, n]) => n.reason)
    .map(([name, n]) => `<p class="h3ps-sogni-banner">${esc(NETWORK_LABEL[name])}: ${esc(n.reason)} Retrying in ${Math.ceil(n.blocked_seconds / 60)} min.</p>`).join("");
  const problem = !state.sdk_installed
    ? `<p class="h3ps-sogni-banner is-error">The Sogni SDK isn't installed in ComfyUI's Python yet, so jobs will wait here. Run: <code>python -m pip install sogni-client</code> in ComfyUI's environment, then restart ComfyUI.</p>`
    : state.message && !["connected", "idle"].includes(state.state) ? `<p class="h3ps-sogni-banner is-error">${esc(state.message)}</p>` : "";
  const summary = ["pending", "active", "completed", "failed"].filter((key) => counts[key]).map((key) => `${counts[key]} ${STATUS_LABEL[key].toLowerCase()}`).join(" · ") || "Empty";
  return `<div class="h3ps-sogni-queue-head"><strong>Sogni queue</strong><small>${esc(summary)}</small></div>
    <div class="h3ps-sogni-queue-tools">
      <button type="button" class="h3ps-secondary-button" data-sogni-send-tabs title="Queue the current prompt of every tab that has one">Send all tabs</button>
      ${blocked ? '<button type="button" class="h3ps-text-button" data-sogni-resume>Retry now</button>' : ""}
      <button type="button" class="h3ps-text-button" data-sogni-clear ${counts.completed || counts.failed || counts.cancelled ? "" : "disabled"}>Clear finished</button>
    </div>
    ${problem}${blocked}
    ${jobs.length ? `<ul class="h3ps-sogni-jobs">${jobs.map((job) => jobRow(job, state.jobs)).join("")}</ul>` : '<p class="h3ps-sogni-empty">Nothing queued. Press Send to Sogni, or Send all tabs.</p>'}`;
}

export function bindSogni({ root, getContext, collectTabs, showToast }) {
  const actions = root.querySelector("[data-sogni-actions]");
  const sendButton = actions.querySelector("[data-sogni-send]");
  const networkButtons = [...actions.querySelectorAll("[data-sogni-network]")];
  const queueToggle = actions.querySelector("[data-sogni-queue-toggle]");
  const settingsToggle = actions.querySelector("[data-sogni-settings-toggle]");
  const settingsPanel = actions.querySelector("[data-sogni-settings]");
  const queuePanel = actions.querySelector("[data-sogni-queue]");
  const countBadge = actions.querySelector("[data-sogni-count]");
  const loraToggle = actions.querySelector("[data-sogni-lora-toggle]");
  const loraPanel = actions.querySelector("[data-sogni-loras]");
  const loraBadge = actions.querySelector("[data-sogni-lora-count]");
  const panels = [[settingsPanel, settingsToggle], [queuePanel, queueToggle], [loraPanel, loraToggle]];
  let loraCatalog = {};
  let loraFilter = "";
  let loraSaveTimer = null;
  let settings = null;
  let state = { jobs: [], networks: {}, sdk_installed: true };
  let known = new Map();
  let pollTimer = null;
  let dragId = null;
  let firstLoad = true;

  async function loadSettings() {
    settings = await call("/settings");
    syncNetwork();
    return settings;
  }

  function syncNetwork() {
    networkButtons.forEach((button) => button.setAttribute("aria-pressed", String(button.dataset.sogniNetwork === settings?.network)));
    const count = settings?.loras?.length || 0;
    loraBadge.hidden = !count;
    loraBadge.textContent = count;
  }

  function openUrl(job) {
    if (job.saved_name) {
      const [subfolder, filename] = job.saved_name.split("/");
      return api.apiURL(`/view?filename=${encodeURIComponent(filename)}&subfolder=${encodeURIComponent(subfolder)}&type=output`);
    }
    return job.video_url;
  }

  function notifyTransitions(jobs) {
    for (const job of jobs) {
      const before = known.get(job.id);
      if (!firstLoad && before && before !== job.status) {
        if (job.status === "completed") {
          showToast("Sogni video ready", `“${job.title.slice(0, 60)}” finished.${job.saved_name ? ` Saved to output/${job.saved_name}.` : ` ${job.message || ""}`}`, null,
            { label: "Open video", onClick: () => window.open(openUrl(job), "_blank", "noopener") });
        } else if (job.status === "failed") {
          showToast("Sogni render failed", `${job.title.slice(0, 60)}: ${job.message || "Unknown error"}`, null, { label: "Show queue", onClick: () => setQueueOpen(true) });
        }
      }
    }
    known = new Map(jobs.map((job) => [job.id, job.status]));
    firstLoad = false;
  }

  function render(next) {
    state = next;
    notifyTransitions(state.jobs);
    const unfinished = state.jobs.filter((job) => UNFINISHED.has(job.status)).length;
    countBadge.hidden = !unfinished;
    countBadge.textContent = unfinished;
    if (!queuePanel.hidden && !dragId) queuePanel.innerHTML = queueMarkup(state);
    schedule(unfinished || !queuePanel.hidden ? POLL_ACTIVE_MS : POLL_IDLE_MS);
  }

  function schedule(ms) {
    clearTimeout(pollTimer);
    pollTimer = setTimeout(refresh, ms);
  }

  async function refresh() {
    try {
      render(await call("/jobs"));
    } catch {
      schedule(POLL_IDLE_MS);
    }
  }

  function setPanel(panel, toggle, open) {
    panel.hidden = !open;
    toggle.setAttribute("aria-expanded", String(open));
  }

  function closeOthers(keep) {
    for (const [panel, toggle] of panels) if (panel !== keep) setPanel(panel, toggle, false);
  }

  function renderLoras() {
    const filtering = document.activeElement?.matches?.("[data-lora-filter]");
    loraPanel.innerHTML = lorasMarkup(loraCatalog, settings?.loras || [], settings?.max_loras || 8, loraFilter);
    if (filtering) {
      const input = loraPanel.querySelector("[data-lora-filter]");
      input.focus();
      input.setSelectionRange(input.value.length, input.value.length);
    }
  }

  async function loadLoras() {
    loraCatalog = {};
    renderLoras();
    try {
      loraCatalog = await call("/loras");
    } catch (error) {
      loraCatalog = { error: error.message };
    }
    if (!loraPanel.hidden) renderLoras();
  }

  async function setLorasOpen(open) {
    if (open) {
      closeOthers(loraPanel);
      if (!await ensureReady()) return;
    }
    setPanel(loraPanel, loraToggle, open);
    if (!open) return;
    if (loraCatalog.loras) renderLoras();
    else await loadLoras();
  }

  function setStack(stack, { rerender = true, delay = 0 } = {}) {
    settings = { ...settings, loras: stack };
    syncNetwork();
    if (rerender) renderLoras();
    clearTimeout(loraSaveTimer);
    loraSaveTimer = setTimeout(() => saveSettings({ loras: stack }, { quiet: true }), delay);
  }

  function setQueueOpen(open) {
    if (open) closeOthers(queuePanel);
    setPanel(queuePanel, queueToggle, open);
    if (open) {
      queuePanel.innerHTML = queueMarkup(state);
      refresh();
    }
  }

  async function setSettingsOpen(open) {
    if (open) {
      closeOthers(settingsPanel);
      try { await loadSettings(); } catch (error) { showToast("Sogni settings unavailable", error.message, error.details); return; }
      settingsPanel.innerHTML = settingsMarkup(settings);
    }
    setPanel(settingsPanel, settingsToggle, open);
    if (open) settingsPanel.querySelector(settings.key_configured ? "[data-sogni-field]" : "[data-sogni-key]")?.focus();
  }

  async function saveSettings(body, { quiet = false } = {}) {
    try {
      settings = await post("/settings", body);
      syncNetwork();
      if (!quiet) {
        setPanel(settingsPanel, settingsToggle, false);
        showToast("Sogni settings saved", settings.key_configured ? "Send to Sogni is ready." : "Add an API key to start sending prompts.");
      }
    } catch (error) {
      showToast("Could not save Sogni settings", error.message, error.details);
    }
  }

  async function ensureReady() {
    if (!settings) {
      try { await loadSettings(); } catch (error) { showToast("Sogni unavailable", error.message, error.details); return false; }
    }
    if (!settings.key_configured) {
      await setSettingsOpen(true);
      showToast("Sogni API key needed", "Paste your key from sogni.ai/keys, then press Send again.");
      return false;
    }
    return true;
  }

  function syncAvailability() {
    const supported = VIDEO_MODES.has(getContext().mode);
    sendButton.disabled = !supported;
    sendButton.title = supported ? "Queue this prompt for rendering on Sogni" : "Send to Sogni works with the H3 video modes";
  }

  async function send() {
    const context = getContext();
    if (!context.prompt.trim()) {
      showToast("Nothing to send", "Generate or write a prompt first.");
      return;
    }
    if (!await ensureReady()) return;
    sendButton.disabled = true;
    try {
      const result = await post("/jobs", { ...context, network: settings.network });
      render(result);
      const job = result.job;
      showToast("Queued for Sogni", `${job.mode} on ${NETWORK_LABEL[job.network]}, ${job.width}×${job.height}, ${job.duration}s. Keep writing; you'll get a notice when it's done.`, null,
        { label: "Show queue", onClick: () => setQueueOpen(true) });
    } catch (error) {
      showToast("Not queued", error.message, error.details);
    } finally {
      syncAvailability();
    }
  }

  async function sendItems(items, label) {
    if (!items.length) {
      showToast("Nothing to send", "None of the tabs has a generated prompt yet.");
      return;
    }
    if (!await ensureReady()) return;
    try {
      const result = await post("/jobs/batch", { items: items.map((item) => ({ ...item, network: settings.network })) });
      render(result);
      const failed = result.errors.length ? ` ${result.errors.length} skipped: ${result.errors.map((e) => `${e.title || `#${e.index + 1}`} (${e.message})`).join("; ")}` : "";
      showToast(label, `${result.added.length} prompt${result.added.length === 1 ? "" : "s"} queued on ${NETWORK_LABEL[settings.network]}.${failed}`, null,
        { label: "Show queue", onClick: () => setQueueOpen(true) });
    } catch (error) {
      showToast("Not queued", error.message, error.details);
    }
  }

  async function jobAction(jobId, action, extra = {}) {
    const job = state.jobs.find((item) => item.id === jobId);
    if (action === "open" && job) {
      window.open(openUrl(job), "_blank", "noopener");
      return;
    }
    try {
      render(await post(`/jobs/${encodeURIComponent(jobId)}/${action}`, extra));
    } catch (error) {
      showToast("Sogni queue", error.message, error.details);
    }
  }

  // The list shows newest first, so a card dropped above another is sent after it.
  function dropTarget(event) {
    const card = event.target.closest?.(".h3ps-sogni-job.is-pending");
    if (!card || !dragId || card.dataset.jobId === dragId) return null;
    const box = card.getBoundingClientRect();
    return { card, above: event.clientY < box.top + box.height / 2 };
  }

  function clearDropMarks() {
    queuePanel.querySelectorAll(".is-drop-above, .is-drop-below").forEach((card) => card.classList.remove("is-drop-above", "is-drop-below"));
  }

  sendButton.addEventListener("click", send);
  networkButtons.forEach((button) => button.addEventListener("click", async () => {
    if (!settings) await loadSettings().catch(() => null);
    if (settings?.network !== button.dataset.sogniNetwork) await saveSettings({ network: button.dataset.sogniNetwork }, { quiet: true });
  }));
  queueToggle.addEventListener("click", () => setQueueOpen(queuePanel.hidden));
  settingsToggle.addEventListener("click", () => setSettingsOpen(settingsPanel.hidden));
  loraToggle.addEventListener("click", () => setLorasOpen(loraPanel.hidden));
  loraPanel.addEventListener("click", (event) => {
    if (event.target.closest("[data-lora-reload]")) return loadLoras();
    if (event.target.closest("[data-lora-clear]")) return setStack([]);
    if (event.target.closest("[data-lora-drop-missing]")) return setStack(settings.loras.filter((item) => loraCatalog.loras.some((lora) => lora.id === item.id)));
  });
  loraPanel.addEventListener("change", (event) => {
    if (event.target.matches("[data-lora-strength]")) return renderLoras();
    const box = event.target.closest("[data-lora-pick]");
    if (!box) return;
    const id = box.closest("[data-lora-id]").dataset.loraId;
    const lora = loraCatalog.loras.find((item) => item.id === id);
    const stack = settings.loras.filter((item) => item.id !== id);
    const max = settings.max_loras || 8;
    if (box.checked) {
      if (stack.length >= max) { box.checked = false; showToast("LoRA limit", `Sogni takes up to ${max} LoRAs per render.`); return; }
      stack.push({ id, name: lora.name, strength: lora.default });
    }
    setStack(stack);
  });
  loraPanel.addEventListener("input", (event) => {
    if (event.target.matches("[data-lora-filter]")) { loraFilter = event.target.value; renderLoras(); return; }
    const slider = event.target.closest("[data-lora-strength]");
    if (!slider) return;
    const row = slider.closest("[data-lora-id]");
    const strength = Number(slider.value);
    row.querySelector("output").textContent = strength;
    setStack(settings.loras.map((item) => item.id === row.dataset.loraId ? { ...item, strength } : item), { rerender: false, delay: 400 });
  });
  settingsPanel.addEventListener("click", (event) => {
    if (event.target.closest("[data-sogni-forget]")) saveSettings({ api_key: "" });
    if (!event.target.closest("[data-sogni-save]")) return;
    const body = {};
    settingsPanel.querySelectorAll("[data-sogni-field]").forEach((field) => {
      body[field.dataset.sogniField] = field.type === "checkbox" ? field.checked : field.type === "number" ? Number(field.value) : field.value;
    });
    const key = settingsPanel.querySelector("[data-sogni-key]").value.trim();
    if (key) body.api_key = key;
    saveSettings(body);
  });
  queuePanel.addEventListener("click", (event) => {
    if (event.target.closest("[data-sogni-send-tabs]")) return sendItems(collectTabs(), "Tabs queued for Sogni");
    if (event.target.closest("[data-sogni-clear]")) return post("/jobs/clear-finished").then(render).catch((error) => showToast("Sogni queue", error.message));
    if (event.target.closest("[data-sogni-resume]")) return post("/resume").then(render).catch((error) => showToast("Sogni queue", error.message));
    const button = event.target.closest("[data-job-action]");
    if (!button) return;
    const id = button.closest("[data-job-id]").dataset.jobId;
    jobAction(id, button.dataset.jobAction, button.dataset.network ? { network: button.dataset.network } : {});
  });
  queuePanel.addEventListener("dragstart", (event) => {
    const card = event.target.closest?.(".h3ps-sogni-job.is-pending");
    if (!card) return;
    dragId = card.dataset.jobId;
    event.dataTransfer.effectAllowed = "move";
    event.dataTransfer.setData("text/plain", dragId);
    card.classList.add("is-dragging");
  });
  queuePanel.addEventListener("dragover", (event) => {
    const target = dropTarget(event);
    clearDropMarks();
    if (!target) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = "move";
    target.card.classList.add(target.above ? "is-drop-above" : "is-drop-below");
  });
  queuePanel.addEventListener("drop", (event) => {
    const target = dropTarget(event);
    if (!target) return;
    event.preventDefault();
    const over = state.jobs.find((job) => job.id === target.card.dataset.jobId);
    const before = target.above ? state.jobs[state.jobs.indexOf(over) + 1] : over;
    const moved = dragId;
    dragId = null;
    if (before?.id !== moved) jobAction(moved, "move", { before: before?.id ?? null });
  });
  queuePanel.addEventListener("dragend", () => {
    dragId = null;
    if (!queuePanel.hidden) queuePanel.innerHTML = queueMarkup(state);
  });
  queuePanel.addEventListener("error", (event) => {
    if (event.target.matches?.(".h3ps-sogni-thumb")) event.target.hidden = true;
  }, true);
  for (const [panel] of panels) {
    panel.addEventListener("keydown", (event) => {
      if (event.key !== "Escape") return;
      event.stopPropagation();
      closeOthers(null);
    });
  }

  loadSettings().catch(() => null);
  refresh();
  syncAvailability();
  return {
    syncAvailability,
    // Called by Writer's prompt queue when a tab's prompt is ready.
    async promptReady(item) {
      if (!settings) await loadSettings().catch(() => null);
      if (settings?.auto_send_queue && settings.key_configured && item.prompt.trim()) await sendItems([item], "Auto-sent to Sogni");
    },
  };
}
