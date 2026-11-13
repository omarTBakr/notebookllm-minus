// Which tab the Visualization view opens, and which address it frames.
//
//     node --test tests/frontend/*.test.mjs

import test from "node:test";
import assert from "node:assert/strict";

import {
  frameUrl,
  pickTab,
  pickView,
  sameOrigin,
  standaloneUrl,
} from "../../src/presentation/web/static/js/visualization_urls.js";

const dashboards = {
  id: "dashboards",
  url: "/grafana/d/app?kiosk",
  views: [
    { id: "app", url: "/grafana/d/app?kiosk" },
    { id: "host", url: "/grafana/d/host?kiosk" },
  ],
};
const traces = {
  id: "traces",
  url: "http://localhost:3001/project/p/traces",
  session_url: "http://localhost:3001/project/p/sessions/{chat_id}",
};
const tasks = { id: "tasks", url: "/flower/tasks" };

test("the remembered tab opens, unless it is no longer offered", () => {
  assert.equal(pickTab([dashboards, traces], "traces"), "traces");
  assert.equal(pickTab([dashboards, traces], "queues"), "dashboards");
  assert.equal(pickTab([dashboards, traces], undefined), "dashboards");
  assert.equal(pickTab([], "traces"), null);
});

test("the remembered dashboard opens, else the first", () => {
  assert.equal(pickView(dashboards, "host"), "host");
  assert.equal(pickView(dashboards, "gone"), "app");
  assert.equal(pickView(tasks, "host"), null);
});

test("a dashboard is framed in the app's own theme", () => {
  assert.equal(frameUrl(dashboards, { viewId: "host", theme: "dark" }), "/grafana/d/host?kiosk&theme=dark");
  assert.equal(frameUrl(dashboards, { viewId: "nope" }), "/grafana/d/app?kiosk");
});

test("traces open on the notebook that is on screen", () => {
  assert.equal(
    frameUrl(traces, { chatId: "abc-123", theme: "dark" }),
    "http://localhost:3001/project/p/sessions/abc-123",
  );
});

test("with no notebook open, traces fall back to the project's list", () => {
  assert.equal(frameUrl(traces, {}), "http://localhost:3001/project/p/traces");
});

test("a plain tab is framed at its own address, whatever the notebook or theme", () => {
  assert.equal(frameUrl(tasks, { chatId: "abc", theme: "dark", viewId: "x" }), "/flower/tasks");
});

test("opened on its own, a dashboard gets Grafana's navigation back", () => {
  assert.equal(standaloneUrl("/grafana/d/app?kiosk&theme=dark"), "/grafana/d/app?theme=dark");
  assert.equal(standaloneUrl("/grafana/d/app?kiosk"), "/grafana/d/app");
  assert.equal(standaloneUrl("/flower/tasks"), "/flower/tasks");
});

test("only a path counts as this origin", () => {
  assert.equal(sameOrigin("/grafana/"), true);
  assert.equal(sameOrigin("http://localhost:3001/"), false);
  assert.equal(sameOrigin("//evil.example/"), false);
});
