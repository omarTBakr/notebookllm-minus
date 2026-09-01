// Two dialogs, both painted and saved from here.
//
// Chat settings (chat-settings-modal): models, temperature, output length,
// splitter, highlight color — everything that belongs to one notebook.
// Appearance (settings-modal): theme and language — everything that belongs
// to the interface instead, and needs no notebook to be open at all.

import { api } from "./api.js";
import { applyLang, t } from "./i18n.js";
import { toast } from "./soon.js";
import { state } from "./state.js";
import { $ } from "./dom.js";

let catalogue = null;
let onLangChange = () => {};

export function bindLangChange(handler) {
  onLangChange = handler;
}

const CONTROLS = ["temperature", "max-tokens", "chunk-size", "overlap-size", "highlight-color"];
const PICKERS = ["model-chat", "model-embed"];

function setDisabled(disabled) {
  CONTROLS.forEach((id) => { const el = $(id); if (el) el.disabled = disabled; });

  // A picker is a div; the button inside it is the thing that disables.
  PICKERS.forEach((id) => {
    const button = $(id)?.querySelector(".picker__button");
    if (button) button.disabled = disabled;
  });

  if (disabled) closePicker();
}

const status = (message) => { $("settings-status").textContent = message ?? ""; };

// --- model names -------------------------------------------------------------

// 8b, 31b, e4b, 137m — a size or variant token, which reads as an acronym.
const SIZE = /\d/;

/** Mirrors SOURCES in utils/model_ids.py. Order does not matter; being the
 *  single list does — the prefix regex, the name cleaner and the badge all
 *  read from here, so teaching the UI about a new source is one edit. */
const SOURCES = ["local", "cloud", "nvidia"];
const QUALIFIED = new RegExp(`^(?:${SOURCES.join("|")})/`);
const SOURCE_PREFIX = new RegExp(`^(${SOURCES.join("|")})/`);

/** Which source an id names. Mirrors split_source: no known prefix is local. */
const sourceOf = (id) => id.match(SOURCE_PREFIX)?.[1] ?? "local";

/** Mirror of split_source in utils/model_ids.py: an id with no known prefix
 *  is local. Chats saved before there was a second host store a bare tag, and
 *  without this they would every one of them read "missing". */
const qualifyId = (id) => (QUALIFIED.test(id) ? id : `local/${id}`);

/** A readable name from an Ollama tag.
 *
 * "local/nomic-embed-text:latest"      -> "Nomic Embed Text"
 * "cloud/gemma4:31b"                   -> "Gemma4 31B"
 * "local/dimavz/whisper-tiny:…"        -> "Whisper Tiny"
 * "nvidia/meta/llama-3.2-11b-instruct" -> "Llama 3.2 11b Instruct"
 *
 * Deliberately one function: tags are a free-for-all and this will need
 * correcting as odd ones turn up.
 */
export function prettyModel(id) {
  const tag = id.replace(QUALIFIED, "");

  // Drop a publisher namespace, but only a leading one — the rest of the tag
  // never contains a slash.
  const withoutOwner = tag.includes("/") ? tag.slice(tag.lastIndexOf("/") + 1) : tag;

  const [name, variant = ""] = withoutOwner.split(":");

  const words = name
    .split(/[-_]/)
    .filter(Boolean)
    // "llama3.1" is written "Llama 3.1" everywhere but in the tag.
    .flatMap((word) => word.split(/(?<=[a-z])(?=\d)/i))
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1));

  // ":latest" says nothing; any other variant is the useful half of the tag.
  if (variant && variant !== "latest") {
    const short = SIZE.test(variant) && variant.length <= 5;
    words.push(
      short ? variant.toUpperCase() : variant.charAt(0).toUpperCase() + variant.slice(1)
    );
  }

  return words.join(" ") || id;
}

/** source -> [css modifier, translation key].
 *
 * "missing" is not a source the backend returns — the picker invents it for an
 * id the catalogue no longer contains, so that a model pulled out from under a
 * chat says so instead of silently reading as local.
 */
const SOURCE_BADGES = {
  local: ["local", "modelLocal"],
  cloud: ["web", "modelWeb"],
  nvidia: ["vendor", "modelNvidia"],
  missing: ["missing", "modelMissing"],
};

function badge(source) {
  const tag = document.createElement("span");
  const [modifier, key] = SOURCE_BADGES[source] ?? [];

  // A source the backend knows and this file does not: label it with its own
  // name rather than mislabelling it "Local", which is what the old two-way
  // `source === "cloud"` test did to everything that was not cloud.
  tag.className = `pill model-tag model-tag--${modifier ?? "vendor"}`;
  tag.textContent = key ? t(key) : source;

  return tag;
}

// --- model pickers -----------------------------------------------------------

// Which picker is open, so opening one closes the other.
let openPicker = null;

function closePicker() {
  if (!openPicker) return;
  openPicker.classList.remove("is-open");
  openPicker.querySelector(".picker__list").hidden = true;
  openPicker.querySelector(".picker__button").setAttribute("aria-expanded", "false");
  openPicker = null;
}

function togglePicker(picker) {
  const wasOpen = openPicker === picker;
  closePicker();
  if (wasOpen) return;

  picker.classList.add("is-open");
  picker.querySelector(".picker__list").hidden = false;
  picker.querySelector(".picker__button").setAttribute("aria-expanded", "true");
  openPicker = picker;
}

/** The button face: the chosen model's name and where it lives. */
function paintCurrent(picker, model, selected) {
  const face = picker.querySelector(".picker__current");
  face.replaceChildren();

  const name = document.createElement("span");
  name.className = "picker__name";
  name.textContent = selected ? prettyModel(selected) : "—";
  face.append(name);

  if (selected) face.append(badge(model ? model.source : "missing"));
}

/** Is *id* anywhere in the catalogue, even in the other list? */
function knownElsewhere(id) {
  if (!catalogue) return false;

  return [...(catalogue.chat ?? []), ...(catalogue.embedding ?? [])]
    .some((model) => model.id === id);
}

function groupHeading(source) {
  const heading = document.createElement("p");
  heading.className = "picker__group";

  const [, key] = SOURCE_BADGES[source] ?? [];
  heading.textContent = key ? t(key) : source;

  return heading;
}

/** Rebuild one picker from the catalogue, one group per source.
 *
 * *selected* is a qualified id. Groups appear in SOURCES order and an empty
 * one is not drawn at all — a cloud host that is down should leave no trace
 * in the list rather than an empty heading.
 */
function fill(picker, options, selected) {
  const list = picker.querySelector(".picker__list");
  list.replaceChildren();

  const wanted = selected ? qualifyId(selected) : null;
  const chosen = options.find((m) => m.id === wanted);

  let rows = options;

  if (wanted && !chosen) {
    if (knownElsewhere(wanted)) {
      // The catalogue has it, this list does not: a model whose kind was
      // reclassified — llama3.1:8b answers an embed probe but reports only
      // `completion`, so it is no longer offered for embedding. The notebook
      // using it still works, so it stays selectable in its own group.
      // Calling that "Missing" would tell the user something untrue.
      rows = [{ id: wanted, source: sourceOf(wanted) }, ...options];
    } else {
      // Genuinely gone: deleted, or on a host that is unreachable right now.
      // Flagged, above the groups, exactly as before.
      list.append(row({ id: wanted, source: "missing" }, picker, true));
    }
  }

  for (const source of SOURCES) {
    const group = rows.filter((model) => model.source === source);

    if (!group.length) continue;

    list.append(groupHeading(source));

    for (const model of group) {
      list.append(row(model, picker, model.id === wanted));
    }
  }

  paintCurrent(picker, chosen ?? rows.find((model) => model.id === wanted), wanted);
}

function row(model, picker, active) {
  const option = document.createElement("button");
  option.type = "button";
  option.className = "picker__option";
  option.setAttribute("role", "option");
  option.setAttribute("aria-selected", String(Boolean(active)));
  if (active) option.classList.add("is-active");

  const name = document.createElement("span");
  name.className = "picker__name";
  name.textContent = prettyModel(model.id);

  option.append(name, badge(model.source));

  if (model.dimensions) {
    const dims = document.createElement("span");
    dims.className = "picker__dims";
    dims.textContent = `${model.dimensions}d`;
    option.append(dims);
  }

  option.addEventListener("click", () => {
    closePicker();
    choose(picker, model.id);
  });

  return option;
}

function choose(picker, id) {
  if (picker.dataset.kind === "chat") {
    saveModels({ generation_model: id });
  } else {
    saveModels({ embedding_model: id }, { rebuilds: true });
  }
}

export async function loadCatalogue() {
  try {
    catalogue = await api.listModels();

    // It arrives after the first paint, so whatever is on screen was drawn
    // without it. Redraw rather than leaving the pickers showing a bare id.
    if (state.notebook) showFor(state.notebook);

  } catch (error) {
    status(error.message);
  }
}

// --- painting the active notebook's values ------------------------------------

function slider(id, valueId, value, format = (v) => v) {
  $(id).value = value;
  $(valueId).textContent = format(value);
}

export function showFor(notebook) {
  if (!notebook) {
    setDisabled(true);
    status("");
    return;
  }

  if (catalogue) {
    fill($("model-chat"), catalogue.chat, notebook.generation_model ?? catalogue.current.chat);
    fill($("model-embed"), catalogue.embedding, notebook.embedding_model ?? catalogue.current.embedding);
  }

  paintModelLine(notebook);

  slider("temperature", "temp-value", notebook.temperature ?? 0.1, (v) => Number(v).toFixed(1));
  slider("max-tokens", "tokens-value", notebook.max_tokens ?? 4096);
  slider("chunk-size", "chunk-value", notebook.chunk_size ?? 500);
  slider("overlap-size", "overlap-value", notebook.overlap_size ?? 50);

  $("highlight-color").value = notebook.highlight_color ?? "#ffff00";

  $("web-search").checked = Boolean(notebook.web_search);

  setDisabled(false);
  status("");
}

// --- saving -------------------------------------------------------------------

async function saveSettings(patch) {
  if (!state.notebook) return;

  try {
    const result = await api.setSettings(state.notebook.chat_id, patch);
    Object.assign(state.notebook, result);
    status(t("saved"));
    // No repaint here on purpose: these come from sliders and a color input,
    // which already show their own value, and redrawing mid-drag would fight
    // the thumb. The model pickers are the opposite case — nothing else
    // updates them.
  } catch (error) {
    status(error.message);
    showFor(state.notebook);
  }
}

async function saveModels(patch, { rebuilds } = {}) {
  if (!state.notebook) return;

  setDisabled(true);
  status(rebuilds ? `${t("reindexed")}…` : "…");

  try {
    const result = await api.setModels(state.notebook.chat_id, patch);
    Object.assign(state.notebook, result);

    // Repaint from the saved notebook. Without this the request succeeded and
    // nothing on screen moved: the picker face still named the old model and
    // the footer still echoed it, so the only way to see that a choice had
    // taken was to reload the page. The failure path has always repainted —
    // it was the success path that left the UI lying.
    showFor(state.notebook);

    status(
      result.reindexed_chunks
        ? `${t("reindexed")} ${result.reindexed_chunks} ${t("chunks")}`
        : t("saved")
    );
  } catch (error) {
    status(error.message);
    showFor(state.notebook);
  } finally {
    setDisabled(false);
  }
}

/** Fire *fn* once the user stops dragging, not on every pixel. */
function debounce(fn, wait = 400) {
  let timer = 0;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), wait);
  };
}

function bindSlider(id, valueId, key, format = (v) => v) {
  const input = $(id);
  const save = debounce((value) => saveSettings({ [key]: value }));

  input.addEventListener("input", () => {
    // The readout follows the thumb; the request waits for the drag to end.
    $(valueId).textContent = format(input.value);
    save(Number(input.value));
  });
}

/** Same debounce-then-PATCH shape as a slider, for a plain string value.
 *  <input type="color"> fires "input" continuously while dragging inside the
 *  native picker, exactly like a range does — the debounce matters here too. */
function bindColor(id, key) {
  const input = $(id);
  const save = debounce((value) => saveSettings({ [key]: value }));

  input.addEventListener("input", () => save(input.value));
}

// --- the dialogs ----------------------------------------------------------------

/** Theme + language. Needs no notebook — both already paint themselves
 *  (bindTheme/applyLang) independently of anything here. */
export function openAppearance() {
  $("settings-modal").hidden = false;
}

/** Models, generation knobs, splitter, highlight color — one notebook's. */
export function openChatSettings() {
  $("chat-settings-modal").hidden = false;
  showFor(state.notebook);
}

/** Whichever modal a click landed inside. Generic because there are now two:
 *  a backdrop or close button says nothing about which one it belongs to. */
function closeModal(el) {
  el.closest(".modal").hidden = true;
}

/** The footer echo of the two models this notebook actually uses.
 *
 * It used to print GET /nlp/health, which is the server's .env default — so
 * it contradicted the pickers directly above it and made a chosen model look
 * ignored. The per-chat model was working all along; this line was lying.
 */
function paintModelLine(notebook) {
  const host = $("backend-info");
  host.replaceChildren();
  host.className = "modelline";

  if (!notebook) return;

  const chat = notebook.generation_model ?? catalogue?.current.chat;
  const embed = notebook.embedding_model ?? catalogue?.current.embedding;

  const known = (id, list) => (list ?? []).find((m) => m.id === qualifyId(id));

  for (const [id, list] of [[chat, catalogue?.chat], [embed, catalogue?.embedding]]) {
    if (!id) continue;

    if (host.childElementCount) {
      const dot = document.createElement("span");
      dot.textContent = "·";
      host.append(dot);
    }

    const name = document.createElement("span");
    name.textContent = prettyModel(id);
    host.append(name, badge(known(id, list)?.source ?? "missing"));
  }
}

/** Still worth surfacing a broken backend, just not in the model line. */
export async function showBackend() {
  try {
    const health = await api.health();

    if (health.status !== "ok") {
      const failed = Object.entries(health.checks)
        .filter(([, c]) => c.status !== "ok")
        .map(([name]) => name);
      toast(`Backend degraded: ${failed.join(", ")}`);
    }
  } catch {
    toast("Backend unreachable");
  }
}

export function bindSettings() {
  $("btn-settings").addEventListener("click", openAppearance);
  $("btn-chat-settings").addEventListener("click", openChatSettings);

  document.querySelectorAll("[data-close-modal]").forEach((el) =>
    el.addEventListener("click", () => closeModal(el))
  );

  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;

    // An open list swallows the first Escape; a modal only closes once
    // there is nothing nested left to dismiss.
    if (openPicker) {
      event.stopPropagation();
      closePicker();
      return;
    }

    // Whichever of the two happens to be open — at most one is, since
    // nothing offers a way to open the second from within the first.
    const open = document.querySelector(".modal:not([hidden])");
    if (open) open.hidden = true;
  });

  PICKERS.forEach((id) => {
    const picker = $(id);
    picker.querySelector(".picker__button")
      .addEventListener("click", () => togglePicker(picker));
  });

  // Clicking anywhere else closes the open list. Capture, because an option's
  // own click stops here first and closes it deliberately.
  document.addEventListener("click", (event) => {
    if (openPicker && !event.target.closest(".picker")) closePicker();
  });

  bindSlider("temperature", "temp-value", "temperature", (v) => Number(v).toFixed(1));
  bindSlider("max-tokens", "tokens-value", "max_tokens");
  bindSlider("chunk-size", "chunk-value", "chunk_size");
  bindSlider("overlap-size", "overlap-value", "overlap_size");
  bindColor("highlight-color", "highlight_color");

  document.querySelectorAll(".btn--lang").forEach((btn) => {
    btn.addEventListener("click", () => {
      applyLang(btn.dataset.lang);
      onLangChange();
    });
  });
}
