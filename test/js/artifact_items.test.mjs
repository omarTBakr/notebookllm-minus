// The rules a generated set applies to its own items.
//
//     node --test test/js/*.test.mjs
//
// The glob, not the directory: `node --test test/js/` fails with
// "Cannot find module" on node 22, which resolves a bare directory
// argument differently than it used to.
//
// Loaded directly rather than through dom.mjs: artifact_items.js has no
// imports and touches no DOM, which is what makes it testable at all — the
// panels around it import half the app.

import test from "node:test";
import assert from "node:assert/strict";

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const source = readFileSync(
  resolve(here, "../../src/web/static/js/artifact_items.js"),
  "utf8",
);

const { citable, score, clampIndex } = await import(
  `data:text/javascript;base64,${Buffer.from(source).toString("base64")}`
);

// --- can this item be opened? --------------------------------------------------

test("an item with both an asset and a chunk can be cited", () => {
  assert.equal(citable({ asset_id: "a1", chunk_order: 4 }), true);
});

test("chunk 0 is citable", () => {
  // The opening chunk of every document. A truthiness test on chunk_order
  // would make the first page of every file uncitable, which is exactly the
  // page a reader is most likely to check.
  assert.equal(citable({ asset_id: "a1", chunk_order: 0 }), true);
});

test("an item without an asset cannot be cited", () => {
  // chunk_order counts within one document, so on its own it names two places
  // in a notebook holding two files. Opening it would be a coin flip.
  assert.equal(citable({ chunk_order: 4 }), false);
  assert.equal(citable({ asset_id: null, chunk_order: 4 }), false);
});

test("an item without a chunk cannot be cited", () => {
  assert.equal(citable({ asset_id: "a1" }), false);
  assert.equal(citable({ asset_id: "a1", chunk_order: null }), false);
});

test("nothing at all is not citable, and does not throw", () => {
  assert.equal(citable(undefined), false);
  assert.equal(citable(null), false);
});

// --- scoring a quiz that is still growing --------------------------------------

const QUESTIONS = [
  { question: "one", options: list(), answer_index: 0 },
  { question: "two", options: list(), answer_index: 2 },
  { question: "three", options: list(), answer_index: 1 },
];

test("only correct picks count", () => {
  const answers = new Map([
    [0, 0], // right
    [1, 3], // wrong
    [2, 1], // right
  ]);

  assert.equal(score(QUESTIONS, answers), 2);
});

test("an unanswered quiz scores nothing", () => {
  assert.equal(score(QUESTIONS, new Map()), 0);
});

test("an answer to a question that no longer exists is ignored", () => {
  // A set is replaced wholesale on every poll and regenerating shortens it.
  // Scoring against an index past the end must not throw — the score simply
  // does not count a question nobody can see.
  const answers = new Map([
    [0, 0],
    [99, 0],
  ]);

  assert.equal(score(QUESTIONS, answers), 1);
});

// --- holding a position in a list that grows -----------------------------------

test("a position inside the list is left alone", () => {
  // The reader stays where they are while a batch lands behind them. Moving
  // them would be the bug this function exists to prevent.
  assert.equal(clampIndex(3, 10), 3);
});

test("a position past the end is pulled back to the last item", () => {
  // Regeneration is the only thing that shortens a set.
  assert.equal(clampIndex(8, 3), 2);
});

test("a negative position is pulled to the start", () => {
  assert.equal(clampIndex(-1, 5), 0);
});

test("an empty list has no position but does not produce -1", () => {
  // -1 would index undefined and render a blank card as though one existed.
  assert.equal(clampIndex(4, 0), 0);
  assert.equal(clampIndex(0, 0), 0);
});

function list() {
  return ["a", "b", "c", "d"];
}
