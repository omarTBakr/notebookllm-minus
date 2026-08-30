// The workspace as three independent panels.
//
// Each one carries its own width and has three states:
//
//   open     a normal column, dragged wider or narrower by the bar beside it
//   tabbed   dragged past its minimum, so it folds down to a labelled rail and
//            its neighbour takes the width back
//   hidden   dismissed outright, from the × or the top bar's toggle group
//
// The grid template is written from here rather than from a stylesheet: the
// number of tracks depends on which panels are showing and which have folded,
// and expressing every combination as a CSS class is far more rules than it is
// worth. layout.css still holds the full-house default, so the first paint is
// correct before this module has run.

import { $ } from "./dom.js";
import { t } from "./i18n.js";
import { toast } from "./soon.js";

const KEY = "notebookllm.panels";

// Left to right, which is also the order of the panels in the markup.
const ORDER = ["sources", "chat", "studio"];

const MIN_W = 180;
const MAX_W = 620;
const DEFAULT_W = 300;

// Keep this in step with --tab-w in panels.css: the drag has to know how wide
// a folded panel actually is to carry on tracking the pointer out of it.
const TAB_W = 44;

// Drag below this and the panel folds. The gap to MIN_W is deliberate — it
// gives the edge some resistance, so a panel does not collapse the instant it
// reaches its narrowest.
const FOLD_AT = 140;

// Below this the panels stack (layout.css) and a pixel width means nothing,
// so the inline template comes off and the media query takes over.
const STACK_AT = 780;

// Chat carries no width: it is the panel that absorbs whatever is left over.
const layout = {
  sources: { width: DEFAULT_W, hidden: false, tabbed: false },
  chat: { width: 0, hidden: false, tabbed: false },
  studio: { width: DEFAULT_W, hidden: false, tabbed: false },
};

let bars = [];

// --- derived state ------------------------------------------------------------

const panelEl = (id) => $(`panel-${id}`);
const shown = () => ORDER.filter((id) => !layout[id].hidden);
const clamp = (px) => Math.max(MIN_W, Math.min(MAX_W, Math.round(px)));

/** The panel that takes the leftover space.
 *
 *  A folded panel is never it — it is pinned to the rail width — so this is
 *  what makes a neighbour grow into the space a fold releases.
 */
function flexId() {
  const open = shown().filter((id) => !layout[id].tabbed);
  if (open.includes("chat")) return "chat";
  return open[open.length - 1] ?? null;
}

/** A drag bar is worth showing only when it has a panel on both sides of it.
 *
 *  Written as "is anything after me still open" rather than naming the
 *  neighbour, so hiding Chat leaves one bar between Sources and Studio
 *  instead of none. A folded panel keeps its bar — that is how it comes back.
 */
function barShown(afterId) {
  if (layout[afterId].hidden) return false;
  return ORDER.slice(ORDER.indexOf(afterId) + 1).some((id) => !layout[id].hidden);
}

/** Which panel a bar actually resizes, and which way the drag runs.
 *
 *  Never the flexible one — that panel has no width of its own to change, so
 *  dragging its edge moves the fixed panel on the other side instead. This is
 *  also what keeps the last unfolded panel unfoldable: it is always the flex.
 */
function dragTarget(afterId) {
  const flex = flexId();
  const next = ORDER.slice(ORDER.indexOf(afterId) + 1).find((id) => !layout[id].hidden);

  if (afterId !== flex) return { id: afterId, sign: 1 };
  if (next && next !== flex) return { id: next, sign: -1 };
  return null;
}

// --- painting -----------------------------------------------------------------

function applyTracks() {
  const workspace = $("workspace");
  if (!workspace) return;

  if (window.innerWidth <= STACK_AT) {
    workspace.style.gridTemplateColumns = "";
    return;
  }

  // Hidden items are display:none, so they claim no track. Emitting exactly
  // one track per *showing* item is what keeps the two lists lined up.
  const flex = flexId();
  const tracks = [];

  ORDER.forEach((id) => {
    if (layout[id].hidden) return;

    if (layout[id].tabbed) tracks.push("var(--tab-w)");
    else tracks.push(id === flex ? "minmax(0, 1fr)" : `${layout[id].width}px`);

    if (barShown(id)) tracks.push("var(--resizer-w)");
  });

  workspace.style.gridTemplateColumns = tracks.join(" ");
}

function paintToggles() {
  document.querySelectorAll("[data-panel-toggle]").forEach((btn) => {
    const id = btn.dataset.panelToggle;
    const open = !layout[id].hidden;
    btn.classList.toggle("is-active", open);
    btn.setAttribute("aria-pressed", String(open));
    btn.title = `${open ? t("hidePanel") : t("showPanel")} — ${t(id)}`;
  });
}

function apply() {
  ORDER.forEach((id) => {
    const el = panelEl(id);
    if (!el) return;
    el.classList.toggle("is-gone", layout[id].hidden);
    el.classList.toggle("is-tab", layout[id].tabbed && !layout[id].hidden);
    // A folded panel is a button in all but name, so it says so.
    el.querySelector(".panel__head")?.setAttribute(
      "title",
      layout[id].tabbed ? `${t("showPanel")} — ${t(id)}` : "",
    );
  });

  bars.forEach((bar) => bar.classList.toggle("is-gone", !barShown(bar.dataset.resizer)));
  paintToggles();
  applyTracks();
}

/** Make sure one panel is actually on screen, unfolding or unhiding it.
 *
 *  Used when something outside the panel needs it visible — clicking a
 *  citation opens the cited document in the sources panel, which is useless
 *  if that panel is folded to a tab or hidden altogether.
 *
 *  A no-op when the panel is already open, so it never disturbs a layout the
 *  user is happy with, and the width is left exactly as they last set it.
 */
export function reveal(id) {
  if (!(id in layout)) return;
  if (!layout[id].hidden && !layout[id].tabbed) return;

  layout[id].hidden = false;
  layout[id].tabbed = false;
  apply();
  save();
}

// --- persistence --------------------------------------------------------------

function save() {
  localStorage.setItem(KEY, JSON.stringify(layout));
}

function restore() {
  let saved = null;
  try {
    saved = JSON.parse(localStorage.getItem(KEY) ?? "null");
  } catch {
    saved = null; // a corrupt entry is not worth reporting; take the defaults
  }
  if (!saved) return;

  ORDER.forEach((id) => {
    const entry = saved[id];
    if (!entry) return;
    if (typeof entry.width === "number") layout[id].width = clamp(entry.width);
    layout[id].hidden = Boolean(entry.hidden);
    layout[id].tabbed = Boolean(entry.tabbed);
  });

  // Never restore into an empty workspace — a stored state with everything
  // hidden would leave nothing on screen to bring anything back with.
  if (shown().length === 0) ORDER.forEach((id) => (layout[id].hidden = false));

  // Nor into one where every panel is a rail and nothing fills the middle.
  // Dragging cannot reach that state; a hand-edited entry can.
  if (flexId() === null) layout[shown()[shown().length - 1]].tabbed = false;
}

// --- behaviour ----------------------------------------------------------------

function setHidden(id, hidden) {
  if (hidden && shown().length === 1) {
    toast(t("panelLast"));
    return;
  }
  layout[id].hidden = hidden;
  apply();
  save();
}

function unfold(id) {
  if (!layout[id].tabbed) return;
  layout[id].tabbed = false;
  layout[id].width = clamp(layout[id].width || DEFAULT_W);
  apply();
  save();
}

function bindBar(bar) {
  /** One step of a drag: fold, unfold, or just a new width. */
  const resize = (dx, start, target) => {
    const panel = layout[target.id];
    const proposed = start + dx * target.sign;

    if (proposed < FOLD_AT) {
      // Fold, but keep the width it had — that is what it reopens to.
      panel.tabbed = true;
    } else {
      panel.tabbed = false;
      panel.width = clamp(proposed);
    }

    apply();
  };

  bar.addEventListener("pointerdown", (event) => {
    if (window.innerWidth <= STACK_AT) return;

    const target = dragTarget(bar.dataset.resizer);
    if (!target) return;

    event.preventDefault();
    const startX = event.clientX;

    // Measure from what is on screen, not from the remembered width: dragging
    // a folded panel has to track the pointer out of the rail, not out of the
    // 300px it will eventually return to.
    const startW = layout[target.id].tabbed ? TAB_W : layout[target.id].width;

    // Capture on the bar, so a fast drag that outruns the pointer keeps
    // feeding this handler instead of being swallowed by whatever it crosses.
    bar.setPointerCapture(event.pointerId);
    bar.classList.add("is-dragging");
    document.body.classList.add("is-resizing");

    // The workspace is pinned to direction:ltr in both languages, so clientX
    // maps straight onto the track order with no mirroring for Arabic.
    const move = (e) => resize(e.clientX - startX, startW, target);

    const done = () => {
      bar.removeEventListener("pointermove", move);
      bar.removeEventListener("pointerup", done);
      bar.removeEventListener("pointercancel", done);
      bar.classList.remove("is-dragging");
      document.body.classList.remove("is-resizing");
      save();
    };

    bar.addEventListener("pointermove", move);
    bar.addEventListener("pointerup", done);
    bar.addEventListener("pointercancel", done);
  });

  // A separator is a real control, so it answers the arrow keys too.
  bar.addEventListener("keydown", (event) => {
    const step = { ArrowLeft: -1, ArrowRight: 1 }[event.key];
    if (!step) return;

    const target = dragTarget(bar.dataset.resizer);
    if (!target) return;

    event.preventDefault();
    const panel = layout[target.id];
    resize(step * (event.shiftKey ? 64 : 16), panel.tabbed ? TAB_W : panel.width, target);
    save();
  });

  // Back to the default width, the way a window manager's edge behaves.
  bar.addEventListener("dblclick", () => {
    const target = dragTarget(bar.dataset.resizer);
    if (!target) return;
    layout[target.id].tabbed = false;
    layout[target.id].width = DEFAULT_W;
    apply();
    save();
  });
}

export function bindPanels() {
  restore();

  bars = Array.from(document.querySelectorAll("[data-resizer]"));
  bars.forEach(bindBar);

  document.querySelectorAll("[data-panel-hide]").forEach((btn) => {
    btn.addEventListener("click", (event) => {
      event.stopPropagation(); // never also read as "unfold me"
      setHidden(btn.dataset.panelHide, true);
    });
  });

  document.querySelectorAll("[data-panel-toggle]").forEach((btn) => {
    const id = btn.dataset.panelToggle;
    btn.addEventListener("click", () => setHidden(id, !layout[id].hidden));
  });

  // A folded panel is all header, and clicking it is how it comes back.
  ORDER.forEach((id) => {
    panelEl(id)
      ?.querySelector(".panel__head")
      ?.addEventListener("click", () => unfold(id));
  });

  // Crossing the stacking width has to add or drop the inline template.
  window.addEventListener("resize", applyTracks);

  apply();
}

/** Redraw the parts built from translated strings. */
export const repaint = apply;
