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

const { citable, score, clampIndex, mindMapBranches, layoutMindMap, linkPath } = await import(
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

// --- mind map branches -----------------------------------------------------------

test("mind map items group by branch, in the order the branches first appear", () => {
  const branches = mindMapBranches([
    { topic: "a", branch: "One" },
    { topic: "b", branch: "Two" },
    { topic: "c", branch: "One" },
  ]);

  assert.deepEqual(
    branches.map((b) => [b.title, b.nodes.map((n) => n.topic)]),
    [["One", ["a", "c"]], ["Two", ["b"]]],
  );
});

test("items not yet grouped come back as one untitled branch", () => {
  const branches = mindMapBranches([{ topic: "a" }, { topic: "b" }]);

  assert.equal(branches.length, 1);
  assert.equal(branches[0].title, "");
  assert.equal(branches[0].nodes.length, 2);
});

test("an empty or missing list is no branches", () => {
  assert.deepEqual(mindMapBranches([]), []);
  assert.deepEqual(mindMapBranches(undefined), []);
  assert.deepEqual(mindMapBranches([null]), []);
});

// --- mind map layout -------------------------------------------------------------

const box = (w, h) => ({ w, h });

test("branches sit level with the middle of their topics, the root with everything", () => {
  const layout = layoutMindMap(
    {
      root: box(100, 30),
      branches: [
        { ...box(80, 20), topics: [box(120, 20), box(120, 20)] },
        { ...box(80, 20), topics: [box(120, 20)] },
      ],
    },
    { gapX: 50, gapY: 10, branchGap: 20 },
  );

  // Branch one's topics span 0..50, so the branch is centred at 25.
  assert.equal(layout.branches[0].y + 10, 25);
  // Branch two starts after 50 + 20, one topic of 20: centred at 80.
  assert.equal(layout.branches[1].y + 10, 80);
  assert.equal(layout.height, 90);
  assert.equal(layout.root.y, (90 - 30) / 2);

  // Columns: root 0..100, branches from 150, topics from 150 + 80 + 50.
  assert.equal(layout.branches[0].x, 150);
  assert.equal(layout.branches[0].topics[0].x, 280);
  assert.equal(layout.width, 400);
});

test("a collapsed branch keeps its place and drops its topics", () => {
  const layout = layoutMindMap({
    root: box(100, 30),
    branches: [{ ...box(80, 20), collapsed: true, topics: [box(120, 20), box(120, 20)] }],
  });

  assert.equal(layout.branches[0].topics.length, 0);
  assert.equal(layout.height, 30, "only the root is left to size the map");
});

test("ungrouped topics hang off the root, in the branch column", () => {
  const layout = layoutMindMap(
    { root: box(100, 30), branches: [{ w: 0, h: 0, topics: [box(120, 20)] }] },
    { gapX: 50 },
  );

  assert.equal(layout.branches[0].topics[0].x, 150);
});

test("an empty map is just the root", () => {
  const layout = layoutMindMap({ root: box(100, 30), branches: [] });

  assert.equal(layout.width, 100);
  assert.equal(layout.height, 30);
});

test("a link runs from the end of one box to the start of the next", () => {
  assert.equal(
    linkPath({ x: 0, y: 0, w: 10, h: 10 }, { x: 30, y: 20, w: 5, h: 10 }),
    "M 10 5 C 20 5, 20 25, 30 25",
  );
});
