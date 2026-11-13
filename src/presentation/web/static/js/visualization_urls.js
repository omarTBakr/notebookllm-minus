// The pure parts of the Visualization view: which tab to open and which
// address to frame. Kept apart from visualization.js so they can be tested
// without a browser (tests/frontend/visualization.test.mjs).

/** The tab to show first: the remembered one if it is still offered. */
export function pickTab(tabs, remembered) {
  if (!tabs.length) return null;
  return (tabs.find((tab) => tab.id === remembered) ?? tabs[0]).id;
}

/** The dashboard to show in the dashboards tab: remembered, else the first. */
export function pickView(tab, remembered) {
  const views = tab?.views ?? [];
  if (!views.length) return null;
  return (views.find((view) => view.id === remembered) ?? views[0]).id;
}

/** The address to frame for *tab*.
 *
 * - dashboards: the chosen Grafana dashboard, in the app's own theme, so a
 *   dark app does not open a white dashboard;
 * - traces: the open notebook's Langfuse session when there is one (a
 *   notebook's id is its session id), else the project's trace list;
 * - anything else: the tab's own address.
 */
export function frameUrl(tab, { viewId = null, chatId = null, theme = null } = {}) {
  if (tab.views?.length) {
    const view = tab.views.find((candidate) => candidate.id === viewId) ?? tab.views[0];
    return theme ? `${view.url}&theme=${theme}` : view.url;
  }

  if (tab.session_url && chatId) {
    return tab.session_url.replace("{chat_id}", encodeURIComponent(chatId));
  }

  return tab.url;
}

/** Whether *url* is served from the page's own origin (a path, not a host). */
export const sameOrigin = (url) => url.startsWith("/") && !url.startsWith("//");

/** The same page for a tab of its own: with Grafana's navigation back.
 *
 * Framed, a dashboard is in kiosk mode because the app already has a header.
 * Opened on its own it should be the full tool.
 */
export function standaloneUrl(url) {
  return url.replace("?kiosk&", "?").replace("?kiosk", "");
}
