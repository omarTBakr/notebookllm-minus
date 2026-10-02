// What the progress bar does with each reading of a source's task.
//
//     node --test tests/frontend/*.test.mjs

import test from "node:test";
import assert from "node:assert/strict";

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const source = readFileSync(
  resolve(here, "../../src/presentation/web/static/js/progress.js"),
  "utf8",
);

const { nextStep } = await import(
  `data:text/javascript;base64,${Buffer.from(source).toString("base64")}`
);

test("a running task is waited on", () => {
  assert.deepEqual(nextStep({ active: true, status: "STARTED" }), { kind: "wait" });
});

test("a finished task with nothing after it is done", () => {
  assert.deepEqual(nextStep({ active: false, status: "SUCCESS", next_task_id: null }), { kind: "done" });
});

test("a task that is not reported at all is done, as before", () => {
  // A poll for an id the table has never heard of answers { active: false }.
  assert.deepEqual(nextStep({ active: false }), { kind: "done" });
});

test("a finished fetch hands the bar to the ingestion it queued", () => {
  // The bar must not stop here: the fetch is the *first* of two tasks, and
  // stopping at its end reloads the list while nothing has been indexed.
  assert.deepEqual(
    nextStep({ active: false, status: "SUCCESS", next_task_id: "ingest-1" }),
    { kind: "follow", taskId: "ingest-1" },
  );
});

test("a failure is a failure even if it names a next task", () => {
  assert.deepEqual(nextStep({ active: false, status: "FAILURE", next_task_id: "x" }), { kind: "fail" });
});

test("a task still running is not followed early", () => {
  assert.deepEqual(nextStep({ active: true, status: "STARTED", next_task_id: "x" }), { kind: "wait" });
});
