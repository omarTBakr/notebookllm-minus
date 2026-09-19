// Right panel: the mind map.
//
// The notebook at the root, branches off it, topics off those, joined by
// curved links coloured per branch. The map is a canvas you move around in --
// drag to pan, Ctrl + scroll or the buttons to zoom -- because a real notebook
// is wider than the panel. "Full screen" gives it the whole window.
//
// Nodes are HTML, laid over an SVG that draws only the links: HTML wraps text
// and takes the app's fonts and focus styles for free, where SVG text would
// need its own line breaking. Positions come from `layoutMindMap`, which is
// pure arithmetic over the measured node sizes.
//
// Like the deck and the quiz it fills while being read: topics arrive
// ungrouped, straight off the root, and move onto their branches when the
// last pass has chosen them.

import { $ } from "./dom.js";
import { citable, cite, generate, resume, takeOverPanel } from "./artifacts.js";
import { layoutMindMap, linkPath, mindMapBranches } from "./artifact_items.js";
import { t } from "./i18n.js";
import { toast } from "./soon.js";
import { state } from "./state.js";

const KIND = "mindmap";

// One hue per branch, in order. Chosen to stay distinct from each other and
// readable as a tint on both the light and the dark surface.
const HUES = [262, 199, 152, 32, 330, 186, 88, 12];

const MIN_SCALE = 0.35;
const MAX_SCALE = 2;

let items = [];
let status = "";

// Kept across polls: the map is redrawn from scratch every time the set
// changes, and none of these may reset under the reader when it does.
const folded = new Set();
const seen = new Set();
let selected = null;
let view = { scale: 1, x: 0, y: 0, moved: false };

export function bindMindMap() {
  const tile = document.querySelector(".studio-card--mind");

  tile?.addEventListener("click", async () => {
    open();

    // Show an existing map before making a new one; regenerating is what the
    // "Generate again" button is for.
    const existing = await resume(KIND, { onItems: setItems, onStatus: setStatus });

    if (!existing) {
      generate(KIND, { onItems: setItems, onStatus: setStatus });
    }
  });

  $("mindmap-close")?.addEventListener("click", close);

  $("mindmap-again")?.addEventListener("click", () => {
    reset();
    render();
    generate(KIND, { onItems: setItems, onStatus: setStatus });
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && $("mindmap")?.classList.contains("is-expanded")) {
      setExpanded(false);
    }
  });

  window.addEventListener("resize", () => {
    if (!view.moved) render();
  });
}

/** Show an existing map when the notebook opens, without generating one. */
export async function repaintMindMap() {
  reset();
  status = "";

  const set = await resume(KIND, { onItems: setItems, onStatus: setStatus });

  if (set && !$("mindmap")?.hidden) render();
}

function reset() {
  items = [];
  folded.clear();
  seen.clear();
  selected = null;
  view = { scale: 1, x: 0, y: 0, moved: false };
}

function open() {
  const panel = $("mindmap");

  if (panel) panel.hidden = false;

  for (const other of ["flashcards", "quiz"]) {
    const el = $(other);

    if (el) el.hidden = true;
  }

  const empty = $("studio-empty");

  if (empty) empty.hidden = true;

  takeOverPanel(true);
  render();
}

function close() {
  setExpanded(false);

  const panel = $("mindmap");

  if (panel) panel.hidden = true;

  takeOverPanel(false);

  const empty = $("studio-empty");

  if (empty) empty.hidden = items.length > 0;
}

function setItems(next) {
  items = next;
  render();
}

function setStatus(next) {
  status = next;
  render();
}

function setExpanded(on) {
  const panel = $("mindmap");

  if (!panel) return;

  panel.classList.toggle("is-expanded", on);
  view.moved = false;
  render();
}

const topicKey = (item) => `${item.asset_id}:${item.chunk_order}:${item.topic}`;
const isRtl = () => document.documentElement.dir === "rtl";

// --- drawing -------------------------------------------------------------------

function render() {
  const panel = $("mindmap");

  if (!panel || panel.hidden) return;

  const counter = $("mindmap-count");
  const note = $("mindmap-status");
  const body = $("mindmap-body");

  if (counter) counter.textContent = items.length ? String(items.length) : "";

  if (note) {
    note.textContent =
      status === "generating" ? (items.length ? t("studioStillAdding") : t("studioWorking")) : "";
    note.hidden = !note.textContent;
  }

  if (!body) return;

  body.replaceChildren();

  if (!items.length) {
    if (status === "generating") body.append(skeleton());
    else body.textContent = t("studioNothingYet");
    return;
  }

  const viewport = document.createElement("div");
  viewport.className = "mm__viewport";

  const canvas = document.createElement("div");
  canvas.className = "mm__canvas";

  const links = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  links.classList.add("mm__links");
  canvas.append(links);

  viewport.append(canvas);
  body.append(toolbar(panel), viewport, inspector());

  draw(canvas, links);
  bindViewport(viewport, canvas);

  if (!view.moved) fit(viewport, canvas);
  else applyView(canvas);
}

/** Build every node, measure it, place it, and draw the links between them. */
function draw(canvas, links) {
  const branches = mindMapBranches(items).map((branch, at) => ({
    ...branch,
    hue: branch.title ? HUES[at % HUES.length] : HUES[0],
  }));

  const rootEl = node("mm-node mm-node--root", state.notebook?.title || t("untitled"));
  canvas.append(rootEl);

  const drawn = branches.map((branch) => {
    const collapsed = folded.has(branch.title);
    const el = branch.title ? branchNode(branch, collapsed) : null;

    if (el) canvas.append(el);

    const topics = collapsed ? [] : branch.nodes.map((item) => topicNode(item, branch.hue));
    topics.forEach((topic) => canvas.append(topic));

    return { branch, el, collapsed, topics };
  });

  // Sizes are only real once the nodes are in the document.
  const size = (el) => (el ? { w: el.offsetWidth, h: el.offsetHeight } : { w: 0, h: 0 });

  const layout = layoutMindMap({
    root: size(rootEl),
    branches: drawn.map(({ el, collapsed, topics }) => ({ ...size(el), collapsed, topics: topics.map(size) })),
  });

  const rtl = isRtl();
  const mirror = (b) => (rtl ? { ...b, x: layout.width - b.x - b.w } : b);
  const place = (el, b) => {
    const m = mirror(b);
    el.style.left = `${m.x}px`;
    el.style.top = `${m.y}px`;
  };

  canvas.style.width = `${layout.width}px`;
  canvas.style.height = `${layout.height}px`;
  links.setAttribute("width", layout.width);
  links.setAttribute("height", layout.height);

  place(rootEl, layout.root);

  const path = (from, to, hue, weight) => {
    // linkPath joins the right edge of its first box to the left edge of its
    // second. Mirrored, the child sits to the left of its parent, so the two
    // are passed the other way round.
    const a = mirror(from);
    const b = mirror(to);

    const p = document.createElementNS("http://www.w3.org/2000/svg", "path");
    p.setAttribute("d", rtl ? linkPath(b, a) : linkPath(a, b));
    p.setAttribute("class", `mm__link mm__link--${weight}`);
    p.style.setProperty("--hue", hue);
    links.append(p);
  };

  drawn.forEach(({ branch, el, topics }, at) => {
    const box = layout.branches[at];
    const parent = el ? box : layout.root;

    if (el) {
      place(el, box);
      path(layout.root, box, branch.hue, "branch");
    }

    topics.forEach((topicEl, i) => {
      place(topicEl, box.topics[i]);
      path(parent, box.topics[i], branch.hue, "topic");
    });
  });
}

function node(className, text) {
  const el = document.createElement("div");
  el.className = className;
  el.textContent = text;
  return el;
}

function branchNode(branch, collapsed) {
  const el = document.createElement("button");
  el.type = "button";
  el.className = "mm-node mm-node--branch";
  el.style.setProperty("--hue", branch.hue);
  el.setAttribute("aria-expanded", String(!collapsed));

  const title = document.createElement("span");
  title.textContent = branch.title;

  const count = document.createElement("span");
  count.className = "mm-node__count";
  count.textContent = String(branch.nodes.length);

  el.append(title, count);
  el.addEventListener("click", () => {
    if (folded.has(branch.title)) folded.delete(branch.title);
    else folded.add(branch.title);
    render();
  });

  return el;
}

function topicNode(item, hue) {
  const key = topicKey(item);
  const el = document.createElement("button");
  el.type = "button";
  el.className = "mm-node mm-node--topic";
  el.style.setProperty("--hue", hue);
  el.textContent = item.topic;

  if (selected?.key === key) el.classList.add("is-selected");

  // Only a topic this page has not drawn before animates in. Every poll
  // redraws the whole map, and re-animating all of it every two seconds would
  // make the map shimmer continuously while it fills.
  if (!seen.has(key)) {
    el.classList.add("is-new");
    seen.add(key);
  }

  el.addEventListener("click", () => {
    selected = selected?.key === key ? null : { key, item, hue };
    render();
  });

  return el;
}

function inspector() {
  const card = document.createElement("div");
  card.className = "mm__inspector";

  if (!selected) {
    card.hidden = true;
    return card;
  }

  const { item, hue } = selected;
  card.style.setProperty("--hue", hue);

  const head = document.createElement("div");
  head.className = "mm__inspector-head";

  if (item.branch) {
    const chip = document.createElement("span");
    chip.className = "mm__chip";
    chip.textContent = item.branch;
    head.append(chip);
  }

  const shut = document.createElement("button");
  shut.type = "button";
  shut.className = "mm__icon-btn";
  shut.setAttribute("aria-label", t("mapClose"));
  shut.innerHTML = ICONS.close;
  shut.addEventListener("click", () => {
    selected = null;
    render();
  });
  head.append(shut);

  const title = document.createElement("h4");
  title.textContent = item.topic;

  const detail = document.createElement("p");
  detail.textContent = item.detail;

  card.append(head, title, detail);

  if (citable(item)) {
    const source = document.createElement("button");
    source.type = "button";
    source.className = "btn btn--ghost mm__cite";
    source.textContent = t("openSource");
    source.addEventListener("click", () => cite(item).catch((e) => toast(e.message)));
    card.append(source);
  }

  return card;
}

function toolbar(panel) {
  const bar = document.createElement("div");
  bar.className = "mm__toolbar";

  const expanded = panel.classList.contains("is-expanded");

  const buttons = [
    ["mapZoomOut", ICONS.minus, () => zoomBy(1 / 1.25)],
    ["mapZoomIn", ICONS.plus, () => zoomBy(1.25)],
    ["mapFit", ICONS.fit, () => {
      view.moved = false;
      render();
    }],
    [expanded ? "mapCollapse" : "mapExpand", expanded ? ICONS.shrink : ICONS.expand, () => setExpanded(!expanded)],
  ];

  for (const [label, icon, action] of buttons) {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "mm__icon-btn";
    b.title = t(label);
    b.setAttribute("aria-label", t(label));
    b.innerHTML = icon;
    b.addEventListener("click", action);
    bar.append(b);
  }

  const hint = document.createElement("span");
  hint.className = "mm__hint";
  hint.textContent = t("mapHint");
  bar.append(hint);

  return bar;
}

function skeleton() {
  const wrap = document.createElement("div");
  wrap.className = "mm__skeleton";

  for (const width of [46, 64, 38, 58, 50]) {
    const bar = document.createElement("span");
    bar.style.width = `${width}%`;
    wrap.append(bar);
  }

  return wrap;
}

// --- moving around ---------------------------------------------------------------

function applyView(canvas) {
  canvas.style.transform = `translate(${view.x}px, ${view.y}px) scale(${view.scale})`;
}

/** Fit the whole map if it will be readable; otherwise show its start. */
function fit(viewport, canvas) {
  const vw = viewport.clientWidth;
  const vh = viewport.clientHeight;
  const w = canvas.offsetWidth;
  const h = canvas.offsetHeight;

  if (!vw || !vh || !w || !h) return;

  const pad = 16;
  const scale = clamp(Math.min((vw - 2 * pad) / w, (vh - 2 * pad) / h, 1), MIN_SCALE, 1);

  // Too big to fit at a readable size: anchor the root at the start edge
  // rather than centring on the middle of the topics, so the reader sees
  // where the map begins and pans from there.
  const fitsWide = w * scale <= vw - 2 * pad;
  const startX = isRtl() ? vw - pad - w * scale : pad;

  view.scale = scale;
  view.x = fitsWide ? (vw - w * scale) / 2 : startX;
  view.y = Math.max(pad, (vh - h * scale) / 2);

  applyView(canvas);
}

function zoomBy(factor, around) {
  const viewport = document.querySelector("#mindmap .mm__viewport");
  const canvas = document.querySelector("#mindmap .mm__canvas");

  if (!viewport || !canvas) return;

  const cx = around?.x ?? viewport.clientWidth / 2;
  const cy = around?.y ?? viewport.clientHeight / 2;
  const next = clamp(view.scale * factor, MIN_SCALE, MAX_SCALE);

  // Keep the point under the cursor (or the centre) where it is.
  view.x = cx - ((cx - view.x) * next) / view.scale;
  view.y = cy - ((cy - view.y) * next) / view.scale;
  view.scale = next;
  view.moved = true;

  applyView(canvas);
}

function bindViewport(viewport, canvas) {
  let drag = null;

  viewport.addEventListener("pointerdown", (event) => {
    // Nodes are buttons; a press on one is a click, not the start of a pan.
    if (event.target.closest("button")) return;

    drag = { x: event.clientX - view.x, y: event.clientY - view.y };
    viewport.setPointerCapture(event.pointerId);
    viewport.classList.add("is-dragging");
  });

  viewport.addEventListener("pointermove", (event) => {
    if (!drag) return;

    view.x = event.clientX - drag.x;
    view.y = event.clientY - drag.y;
    view.moved = true;
    applyView(canvas);
  });

  const end = () => {
    drag = null;
    viewport.classList.remove("is-dragging");
  };

  viewport.addEventListener("pointerup", end);
  viewport.addEventListener("pointercancel", end);

  viewport.addEventListener(
    "wheel",
    (event) => {
      event.preventDefault();

      if (event.ctrlKey || event.metaKey) {
        const box = viewport.getBoundingClientRect();
        zoomBy(event.deltaY < 0 ? 1.1 : 1 / 1.1, { x: event.clientX - box.left, y: event.clientY - box.top });
        return;
      }

      view.x -= event.deltaX;
      view.y -= event.deltaY;
      view.moved = true;
      applyView(canvas);
    },
    { passive: false },
  );
}

const clamp = (value, lo, hi) => Math.min(hi, Math.max(lo, value));

const svg = (inner) =>
  `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${inner}</svg>`;

const ICONS = {
  plus: svg('<path d="M12 5v14M5 12h14"/>'),
  minus: svg('<path d="M5 12h14"/>'),
  fit: svg('<path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5"/>'),
  expand: svg('<path d="M15 3h6v6M9 21H3v-6M21 3l-7 7M3 21l7-7"/>'),
  shrink: svg('<path d="M4 14h6v6M20 10h-6V4M14 10l7-7M3 21l7-7"/>'),
  close: svg('<path d="M18 6 6 18M6 6l12 12"/>'),
};
