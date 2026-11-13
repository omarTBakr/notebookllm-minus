// Visualization: Grafana, Langfuse, Flower and RabbitMQ inside the app.
//
// One button in the top bar opens a view over the workspace with a tab per
// tool, each in a frame. Nothing is loaded until a tab is first opened, and a
// frame that has loaded is kept, so moving between tabs does not reload a
// dashboard that was mid-way through a time range.
//
// The addresses come from the server (GET /chat/visualization), which builds
// them from settings: this module never names a port. See
// application/services/core/visualization.py for why three of the four are
// paths on this origin and Langfuse is not.

import { api } from "./api.js";
import { $ } from "./dom.js";
import { t } from "./i18n.js";
import { state } from "./state.js";
import { frameUrl, pickTab, pickView, sameOrigin, standaloneUrl } from "./visualization_urls.js";

const KEY = "notebookllm.visualization";

let tabs = [];
let activeTab = null;
let activeView = null;
// tab id -> the address its frame currently shows.
const loaded = new Map();

// --- what survives a reload: the last tab and dashboard ------------------------

function remembered() {
  try {
    return JSON.parse(localStorage.getItem(KEY)) ?? {};
  } catch {
    return {};
  }
}

function remember() {
  try {
    localStorage.setItem(KEY, JSON.stringify({ tab: activeTab, view: activeView }));
  } catch { /* private window, storage blocked: the view still works */ }
}

// --- helpers --------------------------------------------------------------------

const tabOf = (id) => tabs.find((tab) => tab.id === id);

/** The palette actually on screen, for Grafana's ?theme=. */
function effectiveTheme() {
  const chosen = document.documentElement.dataset.theme;
  if (chosen === "light" || chosen === "dark") return chosen;
  return window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

function urlFor(tab) {
  return frameUrl(tab, {
    viewId: activeView,
    chatId: state.notebook?.chat_id ?? null,
    theme: effectiveTheme(),
  });
}

function frameFor(tab) {
  const id = `viz-frame-${tab.id}`;
  let frame = document.getElementById(id);

  if (!frame) {
    frame = document.createElement("iframe");
    frame.id = id;
    frame.className = "viz__frame";
    frame.title = t(tab.label);
    frame.setAttribute("loading", "lazy");
    frame.setAttribute("referrerpolicy", "same-origin");
    // Scripts and same-origin are what each tool needs to run at all; forms
    // and popups are its own sign-in and its "open in new tab" links.
    frame.setAttribute("sandbox", "allow-same-origin allow-scripts allow-forms allow-popups allow-downloads");
    $("viz-frames").append(frame);
  }

  return frame;
}

/** Say so when a tool cannot be reached, instead of showing a blank frame.
 *
 * A frame gives a page no error to catch, so the address is asked for first:
 * with its status when it is on this origin, and only "did anything answer"
 * when it is not (Langfuse), which is all a cross-origin probe can tell.
 */
async function reachable(url) {
  try {
    if (sameOrigin(url)) {
      const response = await fetch(url, { method: "GET", credentials: "same-origin" });
      // 401: the session ended. 404: nginx has no such path here. 502: the
      // tool's container is not running.
      return response.status < 400;
    }
    await fetch(url, { mode: "no-cors" });
    return true;
  } catch {
    return false;
  }
}

function showNote(text) {
  const note = $("viz-note");
  note.textContent = text ?? "";
  note.hidden = !text;
}

// --- painting -------------------------------------------------------------------

function paintTabs() {
  const host = $("viz-tabs");
  host.replaceChildren();

  for (const tab of tabs) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "viz__tab";
    button.dataset.tab = tab.id;
    button.setAttribute("role", "tab");
    button.setAttribute("aria-selected", String(tab.id === activeTab));
    button.textContent = t(tab.label);
    button.addEventListener("click", () => show(tab.id));
    host.append(button);
  }
}

function paintViews() {
  const host = $("viz-views");
  const views = tabOf(activeTab)?.views ?? [];

  host.replaceChildren();
  host.hidden = views.length < 2;

  for (const view of views) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "viz__view";
    button.dataset.view = view.id;
    button.setAttribute("aria-pressed", String(view.id === activeView));
    button.textContent = t(view.label);
    button.addEventListener("click", () => {
      activeView = view.id;
      show(activeTab);
    });
    host.append(button);
  }
}

async function show(tabId) {
  const tab = tabOf(tabId);
  if (!tab) return;

  activeTab = tab.id;
  activeView = pickView(tab, activeView ?? remembered().view);
  remember();

  paintTabs();
  paintViews();

  const url = urlFor(tab);
  $("viz-open").href = standaloneUrl(url);

  for (const frame of $("viz-frames").children) frame.hidden = true;
  const frame = frameFor(tab);
  frame.hidden = false;

  // Only navigate when the address changed: a new dashboard, another
  // notebook's traces, a theme switch. Otherwise the frame keeps its place.
  if (loaded.get(tab.id) !== url) {
    showNote(null);
    loaded.set(tab.id, url);
    frame.src = url;

    if (!(await reachable(url)) && activeTab === tab.id) {
      showNote(t("vizNotReachable"));
    }
  }
}

function open() {
  if (!tabs.length) return;

  $("visualization").hidden = false;
  $("btn-visualization").setAttribute("aria-pressed", "true");
  document.body.classList.add("viz-open");

  show(pickTab(tabs, activeTab ?? remembered().tab));
}

function close() {
  $("visualization").hidden = true;
  $("btn-visualization").setAttribute("aria-pressed", "false");
  document.body.classList.remove("viz-open");
  $("btn-visualization").focus();
}

const isOpen = () => !$("visualization").hidden;

// --- public ---------------------------------------------------------------------

/** Labels are drawn in JS, so a language switch has to redraw them. */
export function repaint() {
  if (!tabs.length) return;
  paintTabs();
  paintViews();
  for (const tab of tabs) {
    const frame = document.getElementById(`viz-frame-${tab.id}`);
    if (frame) frame.title = t(tab.label);
  }
}

/** Another notebook is open: its traces are a different address. */
export function notebookChanged() {
  if (isOpen() && activeTab === "traces") show("traces");
}

export async function bindVisualization() {
  const button = $("btn-visualization");
  if (!button) return;

  button.addEventListener("click", () => (isOpen() ? close() : open()));
  $("viz-close").addEventListener("click", close);
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && isOpen()) close();
  });

  try {
    const config = await api.visualization();
    tabs = config.enabled ? config.tabs : [];
  } catch {
    // The view is an extra: a failure here must not stop the app starting.
    tabs = [];
  }

  // Hidden until the server says there is something to show, so a deployment
  // without the tools has no dead button.
  button.hidden = !tabs.length;
}
