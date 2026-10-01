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
 *  because a renamed source may no longer have an extension to read. An
 *  article added from a link is stored as markdown, so it is told apart by
 *  having a link at all. */
const BADGE = (source) => {
  if (source.asset_type === "youtube") return "YT";
  if (source.asset_type === "pdf") return "PDF";
  return source.source_url ? "WEB" : "TXT";
};

const BADGE_CLASS = { YT: "source__icon--yt", WEB: "source__icon--web", TXT: "source__icon--txt" };

function row(source) {
  const item = document.createElement("div");
  item.className = "source";
  item.title = source.name;

  const icon = document.createElement("span");
  icon.className = "source__icon";
  const badge = BADGE(source);
  if (BADGE_CLASS[badge]) icon.classList.add(BADGE_CLASS[badge]);
  icon.textContent = badge;

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
  stopVideo();
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
    // Fit the whole page into the panel by default. Subtract the wrapper's
    // padding from both dimensions because its children are not laid out into
    // that space. This keeps a portrait page from being scaled to the width
    // and then needlessly clipped below the fold.
    const containerWidth = Math.max(canvasWrap.clientWidth - 20, 0) || 280;
    const containerHeight = Math.max(canvasWrap.clientHeight - 20, 0) || 280;
    const unscaled = page.getViewport({ scale: 1 });
    const scale = Math.min(
      containerWidth / unscaled.width,
      containerHeight / unscaled.height,
    );
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

  // Panel widths are user-resizable and the viewport changes on mobile. Keep
  // the page fitted to the new space rather than leaving the old canvas size.
  const resizeObserver = new ResizeObserver(() => {
    renderPage(current).catch((error) => toast(error.message));
  });
  resizeObserver.observe(canvasWrap);

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
  stopVideo();
  $("preview-name").textContent = source.name;
  $("sources-browse").hidden = true;
  $("sources-preview").hidden = false;

  // A source added from a link can be opened where it came from.
  const original = $("preview-original");
  original.hidden = !source.source_url;
  if (source.source_url) original.href = source.source_url;

  const url = api.sourceContentUrl(state.notebook.chat_id, source.asset_id);

  if (source.asset_type === "youtube") {
    await previewVideo(source, url, chunkOrder);
    return;
  }

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

// --- a video source ------------------------------------------------------------

// The one script this frontend loads from elsewhere: the video itself plays
// from YouTube, so its player API has to come from there too. Loaded only
// when a video source is opened, and once.
let youTubeApi = null;

function loadYouTubeApi() {
  if (window.YT?.Player) return Promise.resolve(window.YT);
  if (youTubeApi) return youTubeApi;

  youTubeApi = new Promise((resolve, reject) => {
    const previous = window.onYouTubeIframeAPIReady;
    window.onYouTubeIframeAPIReady = () => {
      previous?.();
      resolve(window.YT);
    };

    const script = document.createElement("script");
    script.src = "https://www.youtube.com/iframe_api";
    script.onerror = () => {
      youTubeApi = null;
      reject(new Error("the YouTube player could not load"));
    };
    document.head.append(script);
  });

  return youTubeApi;
}

// The open video's player and the timer following it, so closing the preview
// or opening another source stops both. A player left running keeps playing
// sound from a panel nobody can see.
let activeVideo = null;

function stopVideo() {
  if (!activeVideo) return;
  clearInterval(activeVideo.timer);
  try {
    activeVideo.player?.destroy?.();
  } catch {
    // Already gone with the DOM it lived in.
  }
  activeVideo = null;
}

/** 83 -> "1:23", 3725 -> "1:02:05": how a video player shows a moment. */
function formatTime(seconds) {
  const total = Math.max(0, Math.floor(seconds));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = String(total % 60).padStart(2, "0");
  return h ? `${h}:${String(m).padStart(2, "0")}:${s}` : `${m}:${s}`;
}

/** Open a video source: the player, and its transcript as clickable lines.
 *
 *  From a citation, the player starts at the cited moment and those lines
 *  are tinted in the notebook's highlight colour. While it plays, the line
 *  being spoken is outlined and kept in view, and clicking any line jumps the
 *  video there — the transcript is how you move around the video.
 */
async function previewVideo(source, url, chunkOrder) {
  status(t("loading"));

  let transcript;
  let range = null;

  try {
    const response = await fetch(url);
    if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
    transcript = await response.json();

    if (chunkOrder !== undefined && chunkOrder !== null) {
      try {
        const located = await api.locateChunk(state.notebook.chat_id, source.asset_id, chunkOrder);
        if (Array.isArray(located.time_range)) range = located.time_range;
      } catch {
        // No moment for this chunk — open the video from the start.
      }
    }
  } catch (error) {
    status(error.message);
    return;
  }

  const segments = transcript.segments ?? [];
  const color = state.notebook?.highlight_color ?? "#ffff00";

  const wrap = document.createElement("div");
  wrap.className = "preview__video";
  const playerBox = document.createElement("div");
  playerBox.className = "preview__player";
  const playerHost = document.createElement("div");
  playerBox.append(playerHost);
  const list = document.createElement("div");
  list.className = "preview__transcript";

  const lines = segments.map((segment) => {
    const line = document.createElement("div");
    line.className = "preview__line";

    const time = document.createElement("span");
    time.className = "preview__time";
    time.textContent = formatTime(segment.start);
    const text = document.createElement("span");
    text.textContent = segment.text;
    line.append(time, text);

    // Overlap, not containment: the cited chunk's range starts and ends on
    // line boundaries, so every line it quotes overlaps it.
    if (range && segment.start < range[1] && segment.end > range[0]) {
      line.classList.add("is-cited");
      line.style.backgroundColor = color;
    }

    line.addEventListener("click", () => seek(segment.start));
    list.append(line);
    return line;
  });

  wrap.append(playerBox, list);
  $("preview-body").replaceChildren(wrap);
  list.querySelector(".is-cited")?.scrollIntoView({ block: "center" });

  const startAt = range ? Math.floor(range[0]) : 0;
  const video = { player: null, timer: null, fallback: null };
  activeVideo = video;

  function seek(seconds) {
    if (video.player?.seekTo) {
      video.player.seekTo(seconds, true);
      video.player.playVideo();
    } else if (video.fallback) {
      // No player API: reload the embed at that moment instead.
      video.fallback.src = embedUrl(transcript.video_id, seconds, true);
    }
  }

  let current = -1;

  function follow() {
    const player = video.player;
    if (!player?.getCurrentTime) return;

    const now = player.getCurrentTime();
    // The last line that has started by now. Linear is fine: a long talk is a
    // few thousand lines, checked twice a second.
    let index = -1;
    for (let i = 0; i < segments.length && segments[i].start <= now; i += 1) index = i;

    if (index === current) return;
    lines[current]?.classList.remove("is-now");
    current = index;
    const line = lines[current];
    if (!line) return;
    line.classList.add("is-now");
    // Only while it plays: a paused video should not drag the list away from
    // whatever the reader scrolled to.
    if (player.getPlayerState?.() === 1) line.scrollIntoView({ block: "nearest" });
  }

  try {
    const YT = await loadYouTubeApi();
    if (activeVideo !== video) return; // another source was opened meanwhile

    video.player = new YT.Player(playerHost, {
      videoId: transcript.video_id,
      host: "https://www.youtube-nocookie.com",
      playerVars: { start: startAt, playsinline: 1, rel: 0 },
      events: {
        onReady: (event) => {
          // From a citation, play the cited moment straight away: that is
          // what clicking "12:34" asked for.
          if (range) event.target.playVideo();
        },
      },
    });
    video.timer = setInterval(follow, 500);
  } catch {
    // The API script was blocked (an extension, a strict network). A plain
    // embed still plays the video at the right moment; lines then reload it.
    const frame = document.createElement("iframe");
    frame.src = embedUrl(transcript.video_id, startAt, Boolean(range));
    frame.allow = "autoplay; encrypted-media; picture-in-picture";
    frame.allowFullscreen = true;
    playerHost.replaceWith(frame);
    video.fallback = frame;
  }
}

function embedUrl(videoId, seconds, autoplay) {
  const params = new URLSearchParams({ start: String(Math.floor(seconds)), rel: "0" });
  if (autoplay) params.set("autoplay", "1");
  return `https://www.youtube-nocookie.com/embed/${encodeURIComponent(videoId)}?${params}`;
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
  return ingestWithProgress(
    file.name,
    EXTENSION(file.name) || "DOC",
    () => api.addSource(state.notebook.chat_id, file),
  );
}

/** Add a source from a link: an online PDF, an article, or a YouTube video.
 *  The server fetches it, so a link it cannot use comes back as an error
 *  saying why — no transcript, no article text, unreachable — shown as is. */
export async function addLink(url) {
  return ingestWithProgress(url, "URL", () => api.addSourceUrl(state.notebook.chat_id, url));
}

/** The pending row, the progress bar and the reload, for either way in. */
async function ingestWithProgress(label, badge, start) {
  if (!state.notebook) {
    toast(t("noNotebookYet"));
    return false;
  }

  // A placeholder row while the source uploads, chunks and embeds.
  const pending = document.createElement("div");
  pending.className = "source source--pending";
  const icon = document.createElement("span");
  icon.className = "source__icon";
  icon.textContent = badge;
  const name = document.createElement("span");
  name.className = "source__name";
  name.textContent = `${label} — ${t("indexing")}`;

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

  try {
    // Returns as soon as the source is stored and the work is queued;
    // chunking and embedding then run on a worker. The row cannot be reloaded
    // until that finishes, or the source would appear with nothing indexed
    // behind it — a notebook that looks ready and retrieves nothing.
    const queued = await start();

    await trackProgress(
      state.notebook.chat_id, queued.task_id, name, meter, fill, queued.filename ?? label
    );

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

/** Poll this upload's task until it finishes, painting the row as it goes.
 *
 *  Resolves when the task reaches a terminal state, and rejects if it failed —
 *  the caller must not reload the source list for an ingestion that did not
 *  complete. A failure to *read* progress is still ignored: a missing bar must
 *  never be the reason an otherwise fine upload reports an error.
 */
function trackProgress(chatId, taskId, nameEl, meter, fill, filename) {
  return new Promise((resolve, reject) => {
    let timer = null;

    const tick = async () => {
      let progress = null;

      try {
        progress = await api.indexingProgress(chatId, taskId);
      } catch {
        // Leave whatever the row is already showing and try again.
        timer = setTimeout(tick, 600);
        return;
      }

      if (progress.status === "FAILURE") {
        reject(new Error(progress.error || t("uploadFailed")));
        return;
      }

      if (!progress.active) {
        resolve();
        return;
      }

      // `t` falls back to the key it was given, so an empty stage rendered
      // the literal string "stage_" in the row. A task that is queued but not
      // yet started has no stage, and neither does the index-building link, so
      // this is the normal state for the first second of every upload.
      const label = progress.stage
        ? t(`stage_${progress.stage}`)
        : t("stage_queued");
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

      timer = setTimeout(tick, 600);
    };

    timer = setTimeout(tick, 250);
  });
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

  $("link-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const input = $("link-input");
    const button = $("link-go");
    const url = input.value.trim();
    if (!url) return;

    // Disabled while the server fetches it, which can take a few seconds for
    // a transcript; cleared only once it landed, so a refused link can be
    // corrected rather than retyped.
    input.disabled = button.disabled = true;
    try {
      if (await addLink(url)) input.value = "";
    } finally {
      input.disabled = button.disabled = false;
    }
  });
}
