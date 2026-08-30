// Left panel: the documents a notebook can answer from.

import { api } from "./api.js";
import { confirmDialog } from "./dialog.js";
import { t } from "./i18n.js";
import { renderInto } from "./markdown.js";
import { toast } from "./soon.js";
import { state } from "./state.js";
import { $ } from "./dom.js";

let onSourcesChanged = () => {};

export function bindSourcesChanged(handler) {
  onSourcesChanged = handler;
}

// Unfolding the panel belongs to panels.js. Registered the same way, so this
// module keeps its single upward dependency direction.
let revealPanel = null;

export function bindRevealPanel(handler) {
  revealPanel = handler;
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
  // checkbox and the two buttons are excluded because none of them is opening.
  item.addEventListener("click", (event) => {
    if (event.target.closest(".source__name, .source__check, .source__delete, .source__download")) return;
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

  // A plain same-origin link, not a fetch+Blob: the bytes already live at a
  // server URL (unlike the answer-download button, which has to build a
  // file client-side from in-memory markdown), so the browser can just
  // navigate to it. The server sets Content-Disposition: attachment with the
  // real filename; `download` here is the fallback name for anything that
  // doesn't honor that header.
  const dl = document.createElement("a");
  dl.className = "source__download";
  dl.href = api.sourceDownloadUrl(state.notebook.chat_id, source.asset_id);
  dl.download = source.name;
  dl.title = t("downloadSource");
  dl.setAttribute("aria-label", `${t("downloadSource")} — ${source.name}`);
  dl.innerHTML =
    '<svg class="ico ico--sm" width="14" height="14" fill="none" stroke="currentColor" ' +
    'stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
    '<use href="#i-download"/></svg>';

  // Destructive and not undoable, so it asks first — the same native dialog
  // the rename flows already use.
  const del = document.createElement("button");
  del.className = "source__delete";
  del.type = "button";
  del.title = t("deleteSource");
  del.setAttribute("aria-label", `${t("deleteSource")} — ${source.name}`);
  del.innerHTML =
    '<svg class="ico ico--sm" width="14" height="14" fill="none" stroke="currentColor" ' +
    'stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
    '<use href="#i-close"/></svg>';
  del.addEventListener("click", () => remove(source));

  item.append(icon, name, dl, del, check);
  return item;
}

/** Delete a source, and with it every chunk and vector derived from it. */
async function remove(source) {
  if (!state.notebook) return;
  const ok = await confirmDialog({
    title: t("deleteSource"),
    message: t("confirmDeleteSource").replace("{name}", source.name),
    confirm: t("deleteSource"),
    danger: true,
  });
  if (!ok) return;

  try {
    await api.deleteSource(state.notebook.chat_id, source.asset_id);
    await load(state.notebook.chat_id);
    onSourcesChanged();
    toast(t("sourceDeleted").replace("{name}", source.name));
  } catch (error) {
    toast(error.message);
  }
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

// pdf.js is the first third-party dependency in this frontend — vendored
// under vendor/, not fetched from a CDN, so the app stays self-contained.
// Loaded lazily, from here alone: the only thing that ever needs it is
// drawing a highlight over a cited passage, and most sessions never open one.
let pdfjsLib = null;

async function loadPdfJs() {
  if (pdfjsLib) return pdfjsLib;

  const mod = await import("./vendor/pdf.min.mjs");
  mod.GlobalWorkerOptions.workerSrc = new URL(
    "./vendor/pdf.worker.min.mjs",
    import.meta.url,
  ).href;

  pdfjsLib = mod;
  return mod;
}

/** Render one PDF page as a canvas, with the cited passage highlighted.
 *
 * Returns whether it worked — the caller falls back to the plain <embed>
 * viewer on false, which covers both "pdf.js could not load" (blocked
 * network, an old browser) and any failure partway through the render.
 *
 * The highlight is drawn only on the page it was computed for. Paging away
 * from it (a deliberate "show me more of the document" action) drops it
 * rather than re-attaching rectangles to a page they were never measured on.
 */
async function renderHighlightedPage(source, url, pageNumber, chunkOrder) {
  let lib;
  try {
    lib = await loadPdfJs();
  } catch {
    return false;
  }

  status(t("loading"));

  let doc;
  let located;
  try {
    [doc, located] = await Promise.all([
      lib.getDocument({
        url,
        standardFontDataUrl: new URL("./vendor/standard_fonts/", import.meta.url).href,
      }).promise,
      // A missing/failed lookup still shows the page — just without a
      // highlight, the same graceful loss a legacy (pre-highlight) chunk
      // already gets from the backend returning highlight: null.
      api.locateChunk(state.notebook.chat_id, source.asset_id, chunkOrder).catch(() => null),
    ]);
  } catch (error) {
    toast(error.message);
    return false;
  }

  const highlight = located?.highlight ?? null;

  const wrap = document.createElement("div");
  wrap.className = "preview__pdfpage";

  const nav = document.createElement("div");
  nav.className = "preview__pdfnav";
  const prevBtn = document.createElement("button");
  prevBtn.type = "button";
  prevBtn.className = "btn btn--outline btn--pill";
  prevBtn.textContent = "‹";
  const label = document.createElement("span");
  label.className = "preview__pdfpagenum";
  const nextBtn = document.createElement("button");
  nextBtn.type = "button";
  nextBtn.className = "btn btn--outline btn--pill";
  nextBtn.textContent = "›";
  nav.append(prevBtn, label, nextBtn);

  const canvasWrap = document.createElement("div");
  canvasWrap.className = "preview__pdfcanvaswrap";
  // Highlight boxes are positioned relative to this inner element, not
  // canvasWrap itself — canvasWrap centers and pads its content, and a
  // rect's left/top would inherit that offset if it anchored there instead.
  const canvasInner = document.createElement("div");
  canvasInner.className = "preview__pdfcanvasinner";
  const canvas = document.createElement("canvas");
  canvasInner.append(canvas);
  canvasWrap.append(canvasInner);

  wrap.append(nav, canvasWrap);
  $("preview-body").replaceChildren(wrap);

  let current = pageNumber;

  async function renderPage(num) {
    current = num;
    label.textContent = `${num} / ${doc.numPages}`;
    prevBtn.disabled = num <= 1;
    nextBtn.disabled = num >= doc.numPages;

    const page = await doc.getPage(num);
    // Fit the panel's content width (clientWidth includes canvasWrap's own
    // padding, which its children are not laid out into — subtracted here so
    // the canvas's pixel size, set below, is not slightly wider than the
    // space it actually has). Height follows from the page's own aspect
    // ratio. Floored at 280 because clientWidth is 0 while the panel is still
    // hidden or mid-animation, and a 0-scale render throws.
    const containerWidth = Math.max(canvasWrap.clientWidth - 20, 0) || 280;
    const unscaled = page.getViewport({ scale: 1 });
    const scale = containerWidth / unscaled.width;
    const viewport = page.getViewport({ scale });

    // No CSS width/height on the canvas: its `width`/`height` attributes,
    // set here, are the only thing deciding its displayed size. That is what
    // keeps it exactly 1:1 with the highlight boxes below, which are
    // positioned in the same pixel units this `scale` produced — any
    // further CSS-driven resize would leave the two mismatched.
    canvas.width = viewport.width;
    canvas.height = viewport.height;
    await page.render({ canvasContext: canvas.getContext("2d"), viewport }).promise;

    canvasInner.querySelectorAll(".preview__highlight").forEach((el) => el.remove());

    if (num === pageNumber && highlight) {
      // "tl": top-left origin, y growing downward — pymupdf's convention and
      // CSS's, so left/top are a straight scale with no coordinate flip.
      for (const [x0, y0, x1, y1] of highlight.r) {
        const box = document.createElement("div");
        box.className = "preview__highlight";
        box.style.left = `${x0 * scale}px`;
        box.style.top = `${y0 * scale}px`;
        box.style.width = `${(x1 - x0) * scale}px`;
        box.style.height = `${(y1 - y0) * scale}px`;
        box.style.backgroundColor = state.notebook?.highlight_color ?? "#ffff00";
        canvasInner.append(box);
      }
    }
  }

  prevBtn.addEventListener("click", () => {
    if (!prevBtn.disabled) renderPage(current - 1).catch((e) => toast(e.message));
  });
  nextBtn.addEventListener("click", () => {
    if (!nextBtn.disabled) renderPage(current + 1).catch((e) => toast(e.message));
  });

  try {
    await renderPage(pageNumber);
  } catch (error) {
    toast(error.message);
    return false;
  }

  return true;
}

/** Open a source at a given page. Called when a citation is clicked.
 *
 *  pageNumber is 1-based — the base the viewer wants, and the base
 *  citations carry. See routes/chat/_pages.py for why that is the only
 *  base allowed to leave the backend. chunkOrder is what the passage a
 *  citation named is actually keyed by, and is what /locate needs to find
 *  its highlight rectangles — see routes/chat/_pages.py again.
 */
export async function openAt(assetId, pageNumber, chunkOrder) {
  const source = state.sources.find((s) => s.asset_id === assetId);

  // The answer outlived the document: cited, then deleted.
  if (!source) {
    toast(t("sourceGone"));
    return;
  }

  // The panel may be folded to a tab or hidden entirely, in which case
  // opening the document into it would be invisible.
  revealPanel?.("sources");

  await preview(source, pageNumber, chunkOrder);
}

async function preview(source, pageNumber, chunkOrder) {
  $("preview-name").textContent = source.name;
  $("sources-browse").hidden = true;
  $("sources-preview").hidden = false;

  const url = api.sourceContentUrl(state.notebook.chat_id, source.asset_id);

  if (source.asset_type === "pdf") {
    // Only a citation click carries both — a page *and* the chunk it names —
    // and that is the one case with a highlight to draw. Rendering every
    // plain browse through pdf.js as well would trade the native viewer's
    // zoom, search and multi-page scroll for a bare canvas, for a feature
    // that case never needs.
    if (pageNumber && chunkOrder !== undefined && chunkOrder !== null) {
      const rendered = await renderHighlightedPage(source, url, pageNumber, chunkOrder);
      if (rendered) return;
      // pdf.js failed to load (blocked network, unsupported browser) or the
      // render itself threw — fall through to the plain viewer below rather
      // than leaving the panel empty.
    }

    const frame = document.createElement("embed");
    frame.className = "preview__pdf";
    frame.type = "application/pdf";
    // #page=N is the PDF Open Parameter, honoured by Chrome, Edge and
    // Firefox's built-in viewers. Safari ignores it and opens at page 1,
    // which is a graceful enough loss to be worth not shipping a PDF
    // renderer for. Set on a freshly created <embed> every time —
    // mutating the hash of a live one does not reliably re-navigate.
    frame.src = pageNumber ? `${url}#page=${pageNumber}` : url;
    $("preview-body").replaceChildren(frame);
    return;
  }

  status(t("loading"));

  try {
    const response = await fetch(url);
    if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
    const text = await response.text();

    // Only a citation click names a chunk, and only a chunk carries a
    // text_range — a plain browse has nothing to highlight. Missing the
    // range (network hiccup, or a chunk ingested before this shipped) just
    // means the document renders without one, same as opening it directly.
    let range = null;
    if (chunkOrder !== undefined && chunkOrder !== null) {
      try {
        const located = await api.locateChunk(state.notebook.chat_id, source.asset_id, chunkOrder);
        if (Array.isArray(located.text_range)) range = located.text_range;
      } catch {
        // No highlight available for this chunk — still show the document.
      }
    }

    const color = state.notebook?.highlight_color ?? "#ffff00";
    const hits = source.asset_type === "markdown"
      ? previewMarkdown(text, range)
      : previewText(text, range);

    for (const el of hits) el.style.backgroundColor = color;
    hits[0]?.scrollIntoView({ block: "center" });
  } catch (error) {
    status(error.message);
  }
}

/** 0-based line index of character offset *pos* within *text*. */
function lineOf(text, pos) {
  const clamped = Math.max(0, Math.min(pos, text.length));
  let count = 0;
  for (let i = 0; i < clamped; i += 1) {
    if (text[i] === "\n") count += 1;
  }
  return count;
}

/** Converts a `[start, end)` character range into the `{start, end}` line
 *  range renderMarkdown expects — see markdown.js for why block, not
 *  character, granularity is what a markdown citation can get.
 */
function lineRangeFor(text, start, end) {
  return { start: lineOf(text, start), end: lineOf(text, Math.max(start, end - 1)) };
}

/** Renders *text* as markdown into the preview panel, highlighting the
 *  block(s) touched by *range* (a `[start, end)` character pair, or null).
 *  Returns the highlighted elements, for the caller to colour and scroll to.
 */
function previewMarkdown(text, range) {
  const body = document.createElement("div");
  body.className = "preview__text md";
  body.dir = "auto";
  renderInto(body, text, range ? lineRangeFor(text, range[0], range[1]) : null);
  $("preview-body").replaceChildren(body);
  return body.querySelectorAll(".cite-highlight");
}

/** Renders *text* as plain text into the preview panel, wrapping *range*
 *  (a `[start, end)` character pair, or null) in a `<mark>`. Returns an
 *  array holding that mark, or empty when there is nothing to highlight.
 */
function previewText(text, range) {
  const body = document.createElement("pre");
  body.className = "preview__text";
  // The file's own language decides which way it reads, not the UI's — an
  // English note in the Arabic interface should still start on the left.
  body.dir = "auto";

  if (!range) {
    // textContent, not innerHTML — this is a file someone uploaded.
    body.textContent = text;
    $("preview-body").replaceChildren(body);
    return [];
  }

  const start = Math.max(0, Math.min(range[0], text.length));
  const end = Math.max(start, Math.min(range[1], text.length));

  body.append(document.createTextNode(text.slice(0, start)));
  const mark = document.createElement("mark");
  mark.className = "preview__mark";
  mark.textContent = text.slice(start, end);
  body.append(mark);
  body.append(document.createTextNode(text.slice(end)));

  $("preview-body").replaceChildren(body);
  return [mark];
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

const ANSWER_NAME = /^answer(\d+)\.md$/i;

/** Save a generated answer as a source of its own.
 *
 * The same move the note editor makes: a File is built in the browser and
 * pushed through the ordinary upload, so the answer is chunked, embedded and
 * retrievable exactly like a document someone dropped in. The backend never
 * learns it came from the model.
 *
 * Named .md because that is what the text is, but typed text/plain — the
 * upload whitelist is ALLOWED_TYPES, and text/markdown is not on it.
 */
export async function saveAnswer(markdown) {
  const text = (markdown ?? "").trim();

  if (!text) {
    toast(t("nothingToSave"));
    return false;
  }

  if (!state.notebook) {
    toast(t("noNotebookYet"));
    return false;
  }

  // Re-read first: another tab may have added answer3 since this one looked,
  // and naming from a stale list is how two answer1.md end up side by side.
  await load(state.notebook.chat_id);

  const highest = state.sources.reduce((max, source) => {
    const match = ANSWER_NAME.exec(source.name ?? "");
    return match ? Math.max(max, Number(match[1])) : max;
  }, 0);

  const file = new File([text], `answer${highest + 1}.md`, { type: "text/plain" });

  // Saving the same answer twice is refused by the content-hash check, which
  // reports itself as a duplicate — add() already surfaces that.
  return add(file);
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

  // Under the row, not beside it: the panel is narrow and the bar has to be
  // able to span the whole width to read as a proportion at all.
  const meter = document.createElement("div");
  meter.className = "source__meter is-waiting";
  const fill = document.createElement("div");
  fill.className = "source__meter-fill";
  meter.append(fill);

  pending.append(icon, name, meter);

  $("sources-empty").hidden = true;
  $("sources-list").append(pending);

  const stopPolling = trackProgress(state.notebook.chat_id, name, meter, fill, file.name);

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
  } finally {
    // The bar belongs to this upload; render() replaces the list on success
    // and the catch removes the row, so either way the poll must stop.
    stopPolling();
  }
}

/** Poll the server's view of this upload and paint it onto the row.
 *
 *  Returns the canceller. Any failure to read progress is ignored on purpose:
 *  a missing bar must never be the reason an otherwise fine upload reports an
 *  error, so the row simply falls back to the indeterminate state.
 */
function trackProgress(chatId, nameEl, meter, fill, filename) {
  let stopped = false;

  const tick = async () => {
    if (stopped) return;

    try {
      const progress = await api.indexingProgress(chatId);

      if (!stopped && progress.active) {
        const label = t(`stage_${progress.stage}`);
        const pct = progress.percent;

        if (typeof pct === "number") {
          // Determinate: the embedding pass knows how many chunks there are.
          meter.classList.remove("is-waiting");
          fill.style.inlineSize = `${pct}%`;
          nameEl.textContent = `${filename} — ${label} ${pct}%`;
        } else {
          // Extracting and chunking have no honest fraction to report.
          meter.classList.add("is-waiting");
          nameEl.textContent = `${filename} — ${label}`;
        }
      }
    } catch {
      // Leave whatever the row is already showing.
    }

    if (!stopped) timer = setTimeout(tick, 600);
  };

  let timer = setTimeout(tick, 250);

  return () => {
    stopped = true;
    clearTimeout(timer);
  };
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
