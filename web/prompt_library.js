// Prompt History and Creative Brief prefills for the H3 Video workspace.
// Kept apart from main.js so upstream merges stay small: main.js only places the
// markup, calls createPromptLibrary() and forwards a few events.

const PREFILL_STORAGE_KEY = "h3ps.briefPrefills.v1";
const ALL_MODES = ["T2VA", "I2VA", "FL2VA", "L2VA", "Reference"];

export const BUILT_IN_PREFILLS = [
  {
    id: "builtin-cinematic",
    name: "Cinematic scene",
    modes: null,
    text: "A cinematic, realistic shot. Setting: \nCharacter: \nAction: \nCamera: slow, steady movement that follows the action.\nMood and light: \nEnd on: ",
  },
  {
    id: "builtin-dialogue",
    name: "Dialogue moment",
    modes: null,
    text: "Two characters talk in a single continuous shot. Location: \nCharacter A says: \"\"\nCharacter B replies: \"\"\nKeep the camera on a gentle push-in and let the ambient sound of the location stay audible under the voices.",
  },
  {
    id: "builtin-product",
    name: "Product showcase",
    modes: null,
    text: "A clean product showcase on a minimal set. Product: \nThe camera orbits slowly while soft key light glides across the surface. Highlight: \nEnd on a centred hero frame with space for a title.",
  },
  {
    id: "builtin-action",
    name: "Action beat",
    modes: null,
    text: "A fast, kinetic action beat. Subject: \nWhat happens: \nCamera: handheld energy, quick reframes, but every hit stays readable.\nSound: sharp impacts and a rising rhythm.",
  },
  {
    id: "builtin-animate-image",
    name: "Bring the picture to life",
    modes: ["I2VA"],
    text: "Start exactly on the uploaded picture and keep its composition, identity and lighting. Bring it to life: \nCamera: \nKeep the motion subtle and natural; nothing new enters the frame unless described here.",
  },
  {
    id: "builtin-first-last",
    name: "First → last frame",
    modes: ["FL2VA"],
    text: "Begin on the first frame and arrive exactly on the last frame. Describe the plausible action in between: \nPacing: \nCamera: ",
  },
  {
    id: "builtin-loop",
    name: "Seamless loop",
    modes: ["L2VA"],
    text: "A seamless loop that starts and ends on the uploaded picture. Repeating motion: \nKeep the camera locked off and the light constant so the loop point is invisible.",
  },
  {
    id: "builtin-reference-roles",
    name: "Reference roles",
    modes: ["Reference"],
    text: "Use <Picture 1> for the character's identity and appearance.\nUse <Picture 2> for the setting.\nUse only the camera movement from <Video 1>.\nScene: ",
  },
];

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char]);
}

function storageGet(storage) {
  try {
    const parsed = JSON.parse(storage?.getItem(PREFILL_STORAGE_KEY) || "[]");
    return Array.isArray(parsed) ? parsed.filter((item) => item && typeof item.text === "string" && typeof item.name === "string") : [];
  } catch {
    return [];
  }
}

function storageSet(storage, prefills) {
  try { storage?.setItem(PREFILL_STORAGE_KEY, JSON.stringify(prefills)); } catch {}
}

export function prefillsForMode(mode, userPrefills = [], builtIns = BUILT_IN_PREFILLS) {
  const fits = (item) => !item.modes || item.modes.includes(mode);
  return {
    user: userPrefills.filter(fits),
    builtIn: builtIns.filter(fits),
  };
}

// Caret position of the first empty "Label: " slot (or empty quotes), so the user can type straight away.
export function firstBlankOffset(text) {
  const match = /: ?(?=\n|$)|""/.exec(text);
  if (!match) return text.length;
  return match[0] === '""' ? match.index + 1 : match.index + match[0].length;
}

export function filterHistory(entries, { mode = "all", query = "" } = {}) {
  const needle = query.trim().toLowerCase();
  return entries.filter((entry) => {
    if (mode !== "all" && entry.mode !== mode) return false;
    if (!needle) return true;
    return `${entry.brief}\n${entry.prompt}\n${entry.model || ""}`.toLowerCase().includes(needle);
  });
}

export function prefillPickerMarkup(icon) {
  return `<div class="h3ps-prefill-picker" data-prefill-picker>
    <button class="h3ps-library-chip" type="button" aria-haspopup="true" aria-expanded="false" data-prefill-toggle title="Start the Creative Brief from a prefill">${icon("spark", 12)}Prefills ${icon("chevron", 11)}</button>
    <div class="h3ps-prefill-menu" role="menu" data-prefill-menu hidden></div>
  </div>`;
}

export function historyButtonMarkup(icon) {
  return `<button class="h3ps-library-chip" type="button" data-history-open title="Recently generated prompts, their pictures and rendered videos">${icon("grid", 13)}History</button>`;
}

export function historyPanelMarkup(icon) {
  return `<section class="h3ps-history" role="dialog" aria-label="Prompt history" data-history-panel hidden>
    <div class="h3ps-history-dialog">
      <header>
        <span><strong>History</strong><small data-history-count>Loading…</small></span>
        <input type="search" placeholder="Search briefs and prompts" aria-label="Search history" data-history-search>
        <div class="h3ps-history-header-actions">
          <button type="button" class="h3ps-text-button" data-history-clear>Clear history</button>
          <button class="h3ps-icon-button" type="button" aria-label="Close history" data-history-close>${icon("close", 16)}</button>
        </div>
      </header>
      <nav class="h3ps-history-filters" data-history-filters>
        ${["all", ...ALL_MODES].map((mode) => `<button type="button" data-history-filter="${mode}">${mode === "all" ? "All modes" : mode}</button>`).join("")}
        <button type="button" class="h3ps-history-gens-filter" data-history-filter="gens" title="Videos rendered with Send to Sogni">${icon("play", 11)} Gens <b data-history-gen-total></b></button>
      </nav>
      <div class="h3ps-history-list" data-history-list></div>
    </div>
  </section>`;
}

function formatDate(seconds) {
  try {
    return new Date(seconds * 1000).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
  } catch {
    return "";
  }
}

function historyEntryMarkup(entry, icon) {
  const pictures = entry.assets.filter((asset) => asset.media_url).length;
  const thumbs = entry.assets.length
    ? `<div class="h3ps-history-thumbs">${entry.assets.map((asset, index) => asset.thumb_url
      ? `<a href="${escapeHtml(asset.media_url || asset.thumb_url)}" target="_blank" rel="noopener" title="${escapeHtml(`${asset.reference || asset.type}: ${asset.filename || ""}`)}" data-history-thumb="${index}"><img src="${escapeHtml(asset.thumb_url)}" alt="" loading="lazy"><b>${escapeHtml(asset.reference || asset.type)}</b></a>`
      : `<span class="h3ps-history-thumb-missing" title="${escapeHtml(asset.filename || "")}">${escapeHtml(asset.reference || asset.type)}<small>${escapeHtml(asset.type)}</small></span>`).join("")}</div>`
    : `<div class="h3ps-history-thumbs is-empty"><span>No media</span></div>`;
  const meta = [entry.mode, entry.duration ? `${entry.duration}s` : null, entry.aspect_ratio, entry.model].filter(Boolean).map(escapeHtml).join(" · ");
  return `<article class="h3ps-history-entry" data-history-entry="${escapeHtml(entry.id)}">
    ${thumbs}
    <div class="h3ps-history-body">
      <header><strong>${escapeHtml(formatDate(entry.created_at))}</strong><small>${meta}</small></header>
      <p class="h3ps-history-brief" title="Creative brief">${escapeHtml(entry.brief || "(empty brief)")}</p>
      <details class="h3ps-history-prompt"><summary>Generated prompt</summary><pre>${escapeHtml(entry.prompt)}</pre></details>
      <footer>
        <button type="button" class="h3ps-history-btn is-primary" data-history-action="load" title="Load this brief and prompt; keep the current media">Use prompt</button>
        ${pictures ? `<button type="button" class="h3ps-history-btn" data-history-action="load-media" title="Load this brief and prompt and replace this mode's media with the saved pictures">Use with pictures</button>` : ""}
        <button type="button" class="h3ps-history-btn" data-history-action="copy">${icon("copy", 12)} Copy</button>
        ${entry.gen_count ? `<button type="button" class="h3ps-history-btn" data-history-action="gens" title="Show the videos rendered from this prompt">${icon("play", 11)} ${entry.gen_count} gen${entry.gen_count === 1 ? "" : "s"}</button>` : ""}
        <button type="button" class="h3ps-history-btn is-quiet" data-history-action="prefill" title="Save this Creative Brief as a reusable prefill">Save brief as prefill</button>
        <span></span>
        <button type="button" class="h3ps-history-btn is-quiet is-destructive" data-history-action="delete">Delete</button>
      </footer>
    </div>
  </article>`;
}

export function filterGens(gens, { query = "", prompt = null } = {}) {
  const needle = query.trim().toLowerCase();
  return gens.filter((gen) => (prompt === null || gen.prompt === prompt)
    && (!needle || `${gen.title || ""}\n${gen.prompt}`.toLowerCase().includes(needle)));
}

function genMarkup(gen, icon) {
  const meta = [
    gen.mode, gen.network === "relaxed" ? "Relaxed" : gen.network === "fast" ? "Fast" : null, gen.resolution,
    gen.duration ? `${gen.duration}s` : null, gen.width && gen.height ? `${gen.width}×${gen.height}` : null,
    gen.loras ? `${gen.loras} LoRA${gen.loras === 1 ? "" : "s"}` : null,
  ].filter(Boolean).map(escapeHtml).join(" · ");
  const title = gen.title || gen.prompt.split(/\s+/).slice(0, 8).join(" ");
  return `<article class="h3ps-history-gen" data-history-gen="${escapeHtml(gen.id)}">
    <video src="${escapeHtml(gen.video_url)}" preload="metadata" controls playsinline></video>
    <div class="h3ps-history-gen-body">
      <strong title="${escapeHtml(gen.prompt)}">${escapeHtml(title)}</strong>
      <small>${escapeHtml(formatDate(gen.created_at))}${meta ? ` · ${meta}` : ""}</small>
      <footer>
        <a class="h3ps-history-btn" href="${escapeHtml(gen.video_url)}" target="_blank" rel="noopener">Open</a>
        <button type="button" class="h3ps-history-btn" data-gen-action="copy">${icon("copy", 12)} Copy prompt</button>
      </footer>
    </div>
  </article>`;
}

/**
 * @param {object} options
 * @param {HTMLElement} options.root  Writer root element (already contains the markup).
 * @param {Function} options.icon
 * @param {Storage} options.storage
 * @param {object} options.api  {listHistory, addHistory, deleteHistory, clearHistory}
 * @param {Function} options.getMode
 * @param {Function} options.getBriefField
 * @param {Function} options.onBriefChanged
 * @param {Function} options.onLoadEntry  (entry, {withMedia}) => Promise
 * @param {Function} options.onCopy  (text) => void
 * @param {Function} options.notify  (title, message, action?) => void
 * @param {Function} options.isBusy
 */
export function createPromptLibrary(options) {
  const { root, icon, storage, api, getMode, getBriefField, onBriefChanged, onLoadEntry, onCopy, notify, isBusy } = options;
  const state = { entries: [], filter: "all", query: "", loaded: false, prefills: storageGet(storage), editing: null, armedClear: false, gens: [], genPrompt: null };
  const prefillToggle = root.querySelector("[data-prefill-toggle]");
  const prefillMenu = root.querySelector("[data-prefill-menu]");
  const panel = root.querySelector("[data-history-panel]");
  const list = root.querySelector("[data-history-list]");

  function savePrefills() { storageSet(storage, state.prefills); }

  function addPrefill(name, text, mode) {
    const cleanName = name.trim() || text.trim().split(/\s+/).slice(0, 6).join(" ") || "Untitled prefill";
    state.prefills.unshift({ id: `user-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 6)}`, name: cleanName.slice(0, 80), text, modes: mode ? [mode] : null });
    savePrefills();
  }

  // ---- Prefills -------------------------------------------------------------

  function prefillEditorMarkup(item, removable) {
    const current = item.modes?.[0] || "";
    const options = ["", ...ALL_MODES].map((value) => `<option value="${value}"${value === current ? " selected" : ""}>${value || "All modes"}</option>`).join("");
    return `<form class="h3ps-prefill-edit" data-prefill-edit="${escapeHtml(item.id)}">
      ${removable ? "" : "<small>Saving creates your own copy; the starter stays as it is.</small>"}
      <input type="text" maxlength="80" value="${escapeHtml(item.name)}" aria-label="Prefill name" data-prefill-edit-name>
      <textarea rows="7" aria-label="Prefill text" data-prefill-edit-text>${escapeHtml(item.text)}</textarea>
      <div class="h3ps-prefill-edit-actions">
        <select aria-label="Show in" data-prefill-edit-mode>${options}</select>
        <button type="button" class="h3ps-history-btn is-quiet" data-prefill-edit-current title="Replace the text above with the current Creative Brief">Use current brief</button>
        <span></span>
        <button type="button" class="h3ps-history-btn is-quiet" data-prefill-edit-cancel>Cancel</button>
        <button type="submit" class="h3ps-secondary-button">Save</button>
      </div>
    </form>`;
  }

  function renderPrefillMenu() {
    const mode = getMode();
    const { user, builtIn } = prefillsForMode(mode, state.prefills);
    const row = (item, removable) => item.id === state.editing ? prefillEditorMarkup(item, removable) : `<div class="h3ps-prefill-row" role="none">
      <button type="button" role="menuitem" data-prefill-apply="${escapeHtml(item.id)}" title="${escapeHtml(item.text)}"><strong>${escapeHtml(item.name)}</strong><small>${escapeHtml(item.modes ? item.modes.join(" · ") : "All modes")}</small></button>
      <button type="button" class="h3ps-prefill-remove" aria-label="Edit ${escapeHtml(item.name)}" title="${removable ? "Edit" : "Edit a copy"}" data-prefill-edit-open="${escapeHtml(item.id)}">${icon("edit", 11)}</button>
      ${removable ? `<button type="button" class="h3ps-prefill-remove" aria-label="Delete ${escapeHtml(item.name)}" data-prefill-remove="${escapeHtml(item.id)}">${icon("close", 11)}</button>` : ""}
    </div>`;
    prefillMenu.innerHTML = `
      ${user.length ? `<h4>My prefills</h4>${user.map((item) => row(item, true)).join("")}` : ""}
      <h4>Starters</h4>${builtIn.map((item) => row(item, false)).join("")}
      <hr>
      <form class="h3ps-prefill-save" data-prefill-save>
        <input type="text" maxlength="80" placeholder="Name for current brief" aria-label="Prefill name" data-prefill-name>
        <label title="Show this prefill only in ${escapeHtml(mode)}"><input type="checkbox" data-prefill-mode-only>${escapeHtml(mode)} only</label>
        <button type="submit" class="h3ps-secondary-button">Save brief</button>
      </form>`;
  }

  function setPrefillEditing(id) {
    state.editing = id;
    renderPrefillMenu();
    const form = id && prefillMenu.querySelector("[data-prefill-edit]");
    if (form) {
      form.scrollIntoView?.({ block: "nearest" });
      form.querySelector("[data-prefill-edit-text]").focus();
    }
  }

  function savePrefillEdit(form) {
    const id = form.dataset.prefillEdit;
    const text = form.querySelector("[data-prefill-edit-text]").value;
    if (!text.trim()) { notify("Nothing to save", "A prefill needs some text."); return; }
    const name = form.querySelector("[data-prefill-edit-name]").value.trim() || text.trim().split(/\s+/).slice(0, 6).join(" ");
    const mode = form.querySelector("[data-prefill-edit-mode]").value;
    const existing = state.prefills.find((item) => item.id === id);
    if (existing) {
      Object.assign(existing, { name: name.slice(0, 80), text, modes: mode ? [mode] : null });
      savePrefills();
    } else {
      addPrefill(name, text, mode || null);
    }
    setPrefillEditing(null);
    notify("Prefill saved", existing ? `“${name}” was updated.` : `“${name}” is now in My prefills.`);
  }

  function setPrefillMenuOpen(open, restoreFocus = false) {
    state.editing = null;
    if (open) renderPrefillMenu();
    prefillMenu.hidden = !open;
    prefillMenu.classList.remove("is-up");
    prefillMenu.style.maxHeight = "";
    if (open) {
      // The input panel scrolls and clips, so keep the menu inside its visible box.
      const bounds = (prefillMenu.closest(".h3ps-input-panel") || root).getBoundingClientRect();
      const menu = prefillMenu.getBoundingClientRect();
      const toggle = prefillToggle.getBoundingClientRect();
      const below = bounds.bottom - toggle.bottom - 10;
      const above = toggle.top - bounds.top - 10;
      const up = menu.height > below && above > below;
      prefillMenu.classList.toggle("is-up", up);
      const room = up ? above : below;
      if (menu.height > room) prefillMenu.style.maxHeight = `${Math.max(120, room)}px`;
    }
    prefillToggle.setAttribute("aria-expanded", String(open));
    if (!open && restoreFocus) prefillToggle.focus();
  }

  function applyPrefill(id) {
    const item = [...state.prefills, ...BUILT_IN_PREFILLS].find((prefill) => prefill.id === id);
    if (!item) return;
    const field = getBriefField();
    const previous = field.value;
    field.value = item.text;
    onBriefChanged();
    setPrefillMenuOpen(false);
    field.focus();
    const caret = firstBlankOffset(item.text);
    field.setSelectionRange(caret, caret);
    if (previous.trim() && previous !== item.text) {
      notify("Prefill applied", `“${item.name}” replaced the Creative Brief.`, { label: "Undo", onClick: () => { field.value = previous; onBriefChanged(); } });
    }
  }

  prefillToggle.addEventListener("click", () => setPrefillMenuOpen(prefillMenu.hidden));
  prefillMenu.addEventListener("click", (event) => {
    const apply = event.target.closest("[data-prefill-apply]");
    if (apply) { applyPrefill(apply.dataset.prefillApply); return; }
    const edit = event.target.closest("[data-prefill-edit-open]");
    if (edit) { setPrefillEditing(edit.dataset.prefillEditOpen); return; }
    if (event.target.closest("[data-prefill-edit-cancel]")) { setPrefillEditing(null); return; }
    if (event.target.closest("[data-prefill-edit-current]")) {
      prefillMenu.querySelector("[data-prefill-edit-text]").value = getBriefField().value;
      return;
    }
    const remove = event.target.closest("[data-prefill-remove]");
    if (remove) {
      state.prefills = state.prefills.filter((item) => item.id !== remove.dataset.prefillRemove);
      savePrefills();
      renderPrefillMenu();
    }
  });
  prefillMenu.addEventListener("submit", (event) => {
    event.preventDefault();
    if (event.target.matches("[data-prefill-edit]")) { savePrefillEdit(event.target); return; }
    const text = getBriefField().value;
    if (!text.trim()) { notify("Nothing to save", "Write a Creative Brief first, then save it as a prefill."); return; }
    const modeOnly = prefillMenu.querySelector("[data-prefill-mode-only]").checked;
    addPrefill(prefillMenu.querySelector("[data-prefill-name]").value, text, modeOnly ? getMode() : null);
    renderPrefillMenu();
    notify("Prefill saved", "The current Creative Brief is now in My prefills.");
  });

  // ---- History --------------------------------------------------------------

  function renderGens() {
    const visible = filterGens(state.gens, { query: state.query, prompt: state.genPrompt });
    root.querySelector("[data-history-count]").textContent = state.loaded
      ? `${visible.length} of ${state.gens.length} render${state.gens.length === 1 ? "" : "s"}`
      : "Loading…";
    const scope = state.genPrompt === null ? "" : `<p class="h3ps-history-gen-scope">Renders of one prompt <button type="button" class="h3ps-text-button" data-gen-action="all">Show all gens</button></p>`;
    list.innerHTML = scope + (visible.length
      ? `<div class="h3ps-history-gen-grid">${visible.map((gen) => genMarkup(gen, icon)).join("")}</div>`
      : `<p class="h3ps-history-empty">${state.loaded ? (state.gens.length ? "No renders match this search." : "Videos rendered with Send to Sogni will appear here.") : "Loading…"}</p>`);
  }

  function renderHistory() {
    root.querySelectorAll("[data-history-filter]").forEach((button) => button.classList.toggle("is-active", button.dataset.historyFilter === state.filter));
    root.querySelector("[data-history-gen-total]").textContent = state.gens.length || "";
    if (state.filter === "gens") { renderGens(); return; }
    const visible = filterHistory(state.entries, { mode: state.filter, query: state.query });
    root.querySelector("[data-history-count]").textContent = state.loaded
      ? `${visible.length} of ${state.entries.length} prompt${state.entries.length === 1 ? "" : "s"}`
      : "Loading…";
    list.innerHTML = visible.length
      ? visible.map((entry) => historyEntryMarkup(entry, icon)).join("")
      : `<p class="h3ps-history-empty">${state.loaded ? (state.entries.length ? "No prompts match this filter." : "Generated H3 prompts will appear here, together with the pictures used for them.") : "Loading…"}</p>`;
  }

  async function refresh() {
    try {
      const result = await api.listHistory();
      state.entries = result.entries || [];
      state.gens = result.gens || [];
      state.loaded = true;
    } catch (error) {
      state.loaded = true;
      notify(error.code || "History unavailable", error.message);
    }
    renderHistory();
  }

  function setHistoryOpen(open) {
    panel.hidden = !open;
    root.classList.toggle("is-history-open", open);
    disarmClear();
    if (open) {
      state.filter = "all";
      state.genPrompt = null;
      renderHistory();
      refresh();
      requestAnimationFrame(() => root.querySelector("[data-history-search]")?.focus());
    } else {
      root.querySelector("[data-history-open]")?.focus();
    }
  }

  function disarmClear() {
    state.armedClear = false;
    const button = root.querySelector("[data-history-clear]");
    button.textContent = "Clear history";
    button.classList.remove("is-destructive");
  }

  async function record(payload) {
    try {
      const result = await api.addHistory(payload);
      if (result?.entry) state.entries.unshift(result.entry);
      if (!panel.hidden) renderHistory();
    } catch (error) {
      console.warn("[H3 Prompt Writer] Prompt history was not saved:", error);
    }
  }

  root.querySelector("[data-history-open]").addEventListener("click", () => setHistoryOpen(true));
  root.querySelector("[data-history-close]").addEventListener("click", () => setHistoryOpen(false));
  panel.addEventListener("click", (event) => { if (event.target === panel) setHistoryOpen(false); });
  root.querySelector("[data-history-search]").addEventListener("input", (event) => { state.query = event.target.value; renderHistory(); });
  root.querySelector("[data-history-filters]").addEventListener("click", (event) => {
    const button = event.target.closest("[data-history-filter]");
    if (!button) return;
    state.filter = button.dataset.historyFilter;
    state.genPrompt = null;
    renderHistory();
  });
  root.querySelector("[data-history-clear]").addEventListener("click", async (event) => {
    if (!state.entries.length && !state.gens.length) return;
    if (!state.armedClear) {
      state.armedClear = true;
      event.currentTarget.textContent = `Delete all ${state.entries.length}?`;
      event.currentTarget.classList.add("is-destructive");
      return;
    }
    disarmClear();
    try {
      await api.clearHistory();
      state.entries = [];
      state.gens = [];
      renderHistory();
      notify("History cleared", "Saved prompts, their pictures and the list of gens were deleted. Rendered videos stay in output/h3_sogni.");
    } catch (error) {
      notify(error.code || "Clear failed", error.message);
    }
  });
  list.addEventListener("click", async (event) => {
    const genButton = event.target.closest("[data-gen-action]");
    if (genButton) {
      if (genButton.dataset.genAction === "all") { state.genPrompt = null; renderHistory(); return; }
      const gen = state.gens.find((item) => item.id === genButton.closest("[data-history-gen]")?.dataset.historyGen);
      if (gen) onCopy(gen.prompt);
      return;
    }
    const button = event.target.closest("[data-history-action]");
    if (!button) return;
    const entry = state.entries.find((item) => item.id === button.closest("[data-history-entry]")?.dataset.historyEntry);
    if (!entry) return;
    const action = button.dataset.historyAction;
    if (action === "copy") onCopy(entry.prompt);
    else if (action === "gens") {
      state.filter = "gens";
      state.genPrompt = entry.prompt;
      renderHistory();
    }
    else if (action === "prefill") {
      if (!entry.brief.trim()) { notify("Nothing to save", "This entry has an empty Creative Brief."); return; }
      addPrefill(`${entry.mode} · ${entry.brief.trim().split(/\s+/).slice(0, 5).join(" ")}`, entry.brief, null);
      notify("Prefill saved", "The brief is now in My prefills.");
    } else if (action === "delete") {
      try {
        await api.deleteHistory(entry.id);
        state.entries = state.entries.filter((item) => item.id !== entry.id);
        renderHistory();
      } catch (error) {
        notify(error.code || "Delete failed", error.message);
      }
    } else if (action === "load" || action === "load-media") {
      if (isBusy()) { notify("Writer is busy", "Wait for the current request to finish."); return; }
      button.disabled = true;
      try {
        const loaded = await onLoadEntry(entry, { withMedia: action === "load-media" });
        if (loaded) setHistoryOpen(false);
      } finally {
        button.disabled = false;
      }
    }
  });

  // Capture phase: the menu's own click handler re-renders it, which would detach the clicked button.
  root.addEventListener("click", (event) => {
    if (!prefillMenu.hidden && !event.target.closest("[data-prefill-picker]")) setPrefillMenuOpen(false);
  }, true);

  return {
    record,
    open: () => setHistoryOpen(true),
    get historyOpen() { return !panel.hidden; },
    /** Close the topmost library surface. Returns true when something was closed. */
    closeTopmost() {
      if (state.editing) { setPrefillEditing(null); return true; }
      if (!prefillMenu.hidden) { setPrefillMenuOpen(false, true); return true; }
      if (!panel.hidden) { setHistoryOpen(false); return true; }
      return false;
    },
  };
}
