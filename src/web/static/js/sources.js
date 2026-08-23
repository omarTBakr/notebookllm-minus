// Left panel: the documents a notebook can answer from.

import { api } from "./api.js";
import { t } from "./i18n.js";
import { toast } from "./soon.js";
import { state } from "./state.js";
import { $ } from "./dom.js";

let onSourcesChanged = () => {};

export function bindSourcesChanged(handler) {
  onSourcesChanged = handler;
}

const EXTENSION = (name) => (name.split(".").pop() || "").toUpperCase().slice(0, 4);

/** The badge on a row. Derived from the asset type rather than the filename,
 *  because a renamed source may no longer have an extension to read. */
const BADGE = (source) => (source.asset_type === "pdf" ? "PDF" : "TXT");

function row(source) {
  const item = document.createElement("div");
  item.className = "source";
  item.title = source.name;

  const icon = document.createElement("span");
  icon.className = "source__icon";
  if (source.asset_type !== "pdf") icon.classList.add("source__icon--txt");
  icon.textContent = BADGE(source);

  const name = document.createElement("span");
  name.className = "source__name";
  name.textContent = source.name;

  // Double-click to rename, the way a file manager does it. No extra control
  // in the row: the list is narrow and every source already has a checkbox.
  name.addEventListener("dblclick", () => edit(name, source));

  // Clicking the row opens it. The name is excluded so a double-click to
  // rename does not also open a preview behind the edit field, and the
  // checkbox is excluded because ticking is not opening.
  item.addEventListener("click", (event) => {
    if (event.target.closest(".source__name, .source__check")) return;
    preview(source);
  });

  // Which sources a question searches. Unticking one narrows retrieval to
  // the rest rather than merely hiding it from the list.
  const check = document.createElement("input");
  check.type = "checkbox";
  check.className = "source__check";
  check.checked = source.selected !== false;
  check.title = source.name;
  check.addEventListener("change", () => toggle(source.asset_id, check.checked));

  item.append(icon, name, check);
  return item;
}

/** Sources currently switched on — what a question will actually search. */
export const selectedSources = () => state.sources.filter((s) => s.selected !== false);

// --- previewing one source ----------------------------------------------------

/** Show the list again. Also the state the panel starts in. */
export function closePreview() {
  $("sources-preview").hidden = true;
  $("sources-browse").hidden = false;
  // Drop the content so a PDF stops holding its bytes in memory.
  $("preview-body").replaceChildren();
}

function status(message) {
  const line = document.createElement("p");
  line.className = "preview__status";
  line.textContent = message;
  $("preview-body").replaceChildren(line);
}

async function preview(source) {
  $("preview-name").textContent = source.name;
  $("sources-browse").hidden = true;
  $("sources-preview").hidden = false;

  const url = api.sourceContentUrl(state.notebook.chat_id, source.asset_id);

  // A PDF is handed to the browser's own viewer; only text is fetched.
  if (source.asset_type === "pdf") {
    const frame = document.createElement("embed");
    frame.className = "preview__pdf";
    frame.type = "application/pdf";
    frame.src = url;
    $("preview-body").replaceChildren(frame);
    return;
  }

  status(t("loading"));

  try {
    const response = await fetch(url);
    if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);

    const body = document.createElement("pre");
    body.className = "preview__text";
    // The file's own language decides which way it reads, not the UI's — an
    // English note in the Arabic interface should still start on the left.
    body.dir = "auto";
    // textContent, not innerHTML — this is a file someone uploaded.
    body.textContent = await response.text();
    $("preview-body").replaceChildren(body);
  } catch (error) {
    status(error.message);
  }
}

/** Swap the name for a text field. Enter commits, Escape and blur cancel. */
function edit(name, source) {
  const field = document.createElement("input");
  field.type = "text";
  field.className = "source__rename";
  field.value = source.name;

  // A blur fires when Enter removes the field too, so the commit path has to
  // be able to say "already handled" rather than run twice.
  let settled = false;

  const close = () => {
    if (settled) return;
    settled = true;
    field.replaceWith(name);
  };

  const commit = () => {
    if (settled) return;
    const next = field.value.trim();
    close();
    if (next && next !== source.name) rename(source, next);
  };

  field.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      commit();
    } else if (event.key === "Escape") {
      event.preventDefault();
      close();
    }
  });

  field.addEventListener("blur", commit);

  name.replaceWith(field);
  field.focus();
  field.select();
}

async function rename(source, name) {
  const previous = source.name;
  source.name = name;
  render();

  try {
    await api.renameSource(state.notebook.chat_id, source.asset_id, name);
  } catch (error) {
    // Put the old name back if the server refused it.
    source.name = previous;
    render();
    toast(error.message);
  }
}

async function toggle(assetId, selected) {
  const source = state.sources.find((s) => s.asset_id === assetId);
  if (source) source.selected = selected;

  const excluded = state.sources
    .filter((s) => s.selected === false)
    .map((s) => s.asset_id);

  try {
    await api.selectSources(state.notebook.chat_id, excluded);
    render();
    onSourcesChanged();
  } catch (error) {
    // Put the tick back if the server refused it.
    if (source) source.selected = !selected;
    render();
    toast(error.message);
  }
}

async function toggleAll(selected) {
  state.sources.forEach((s) => { s.selected = selected; });

  const excluded = selected ? [] : state.sources.map((s) => s.asset_id);

  try {
    await api.selectSources(state.notebook.chat_id, excluded);
    render();
    onSourcesChanged();
  } catch (error) {
    await load(state.notebook.chat_id);
    toast(error.message);
  }
}

export function render() {
  const list = $("sources-list");
  const empty = $("sources-empty");
  const toolbar = $("sources-toolbar");

  list.replaceChildren();

  const count = state.sources.length;

  empty.hidden = count > 0;
  toolbar.hidden = count === 0;

  const all = $("select-all");
  if (all) {
    const chosen = selectedSources().length;
    all.checked = chosen === count && count > 0;
    // Some but not all: the box shows neither ticked nor empty.
    all.indeterminate = chosen > 0 && chosen < count;
  }

  state.sources.forEach((source) => list.append(row(source)));
}

export async function load(chatId) {
  closePreview();

  if (!chatId) {
    state.sources = [];
    render();
    return;
  }

  try {
    const { assets } = await api.listSources(chatId);
    state.sources = assets;
  } catch {
    // A failing source list must not take the chat down with it.
    state.sources = [];
  }

  render();
}

/** Upload a file as a source. Returns whether it landed — the note editor
 *  needs to know, since a refused upload must not clear what was typed. */
export async function add(file) {
  if (!state.notebook) {
    toast(t("noNotebookYet"));
    return false;
  }

  // A placeholder row while the file uploads, chunks and embeds — that whole
  // pipeline runs in one request and can take a while on a local model.
  const pending = document.createElement("div");
  pending.className = "source source--pending";
  const icon = document.createElement("span");
  icon.className = "source__icon";
  icon.textContent = EXTENSION(file.name) || "DOC";
  const name = document.createElement("span");
  name.className = "source__name";
  name.textContent = `${file.name} — ${t("indexing")}`;
  pending.append(icon, name);

  $("sources-empty").hidden = true;
  $("sources-list").append(pending);

  try {
    await api.addSource(state.notebook.chat_id, file);
    await load(state.notebook.chat_id);
    onSourcesChanged();
    return true;
  } catch (error) {
    pending.remove();
    render();
    toast(error.message);
    return false;
  }
}

export function bindSources() {
  $("select-all").addEventListener("change", (event) => toggleAll(event.target.checked));

  $("preview-back").addEventListener("click", closePreview);

  $("file-input").addEventListener("change", (event) => {
    const [file] = event.target.files;
    if (file) add(file);
    // Reset so choosing the same file twice still fires a change event.
    event.target.value = "";
  });
}
