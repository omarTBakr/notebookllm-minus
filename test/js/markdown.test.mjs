// The markdown renderer, table support in particular.
//
//     node --test test/js/
//
// No dependencies and no package.json: node 22 ships the runner, and dom.mjs
// stands in for the browser.

import test from "node:test";
import assert from "node:assert/strict";

import { render, tableText } from "./dom.mjs";

const TABLE = ["| Model | Size |", "|-------|------|", "| Gemma | 4B   |"].join("\n");

test("a header, a separator and a row become a table", () => {
  assert.deepEqual(tableText(render(TABLE)), [
    ["Model", "Size"],
    ["Gemma", "4B"],
  ]);
});

test("the separator row is never rendered as content", () => {
  const text = render(TABLE).textContent;

  assert.ok(!text.includes("---"), `separator leaked into the output: ${text}`);
});

test("a header with no separator stays a paragraph", () => {
  // Which is also what a table looks like mid-stream, before its second line
  // has arrived. It must not half-render.
  const root = render("| Model | Size |");

  assert.equal(tableText(root), null);
  assert.equal(root.find("p").length, 1);
});

test("outer pipes are optional", () => {
  const root = render(["Model | Size", "------|-----", "Gemma | 4B"].join("\n"));

  assert.deepEqual(tableText(root), [
    ["Model", "Size"],
    ["Gemma", "4B"],
  ]);
});

test("alignment colons set text-align and are stripped from the cell", () => {
  const root = render(
    ["| L | C | R |", "|:--|:-:|--:|", "| a | b | c |"].join("\n"),
  );
  const [table] = root.find("table");

  assert.deepEqual(
    table.find("th").map((cell) => cell.style.textAlign),
    ["left", "center", "right"],
  );
  assert.deepEqual(tableText(root)[0], ["L", "C", "R"]);
});

test("emphasis does not bleed across a cell boundary", () => {
  // The bug that made this worth testing: inlineNodes used to run on the whole
  // joined line, so the underscores in two different cells paired up and
  // italicised the pipe between them.
  const root = render(["| a_b | c_d |", "|-----|-----|", "| e_f | g_h |"].join("\n"));

  assert.deepEqual(tableText(root), [
    ["a_b", "c_d"],
    ["e_f", "g_h"],
  ]);
  assert.equal(root.find("em").length, 0);
});

test("emphasis inside a single cell still renders", () => {
  const root = render(["| **bold** | x |", "|---|---|", "| y | z |"].join("\n"));

  assert.equal(root.find("strong").length, 1);
  assert.equal(root.find("strong")[0].textContent, "bold");
});

test("a short row is padded to the header width", () => {
  const root = render(["| a | b | c |", "|---|---|---|", "| 1 |"].join("\n"));

  assert.deepEqual(tableText(root), [
    ["a", "b", "c"],
    ["1", "", ""],
  ]);
});

test("an escaped pipe stays inside its cell", () => {
  const root = render(["| a \\| b | c |", "|---|---|", "| d | e |"].join("\n"));

  assert.deepEqual(tableText(root)[0], ["a | b", "c"]);
});

test("a blank line ends the table", () => {
  const root = render(
    [TABLE, "", "After the table."].join("\n"),
  );

  assert.equal(tableText(root).length, 2);
  assert.ok(root.textContent.includes("After the table."));
});

test("a table inside a code fence is left literal", () => {
  const root = render(["```", TABLE, "```"].join("\n"));

  assert.equal(root.find("table").length, 0);
  assert.equal(root.find("pre").length, 1);
  assert.ok(root.find("pre")[0].textContent.includes("|-------|"));
});

test("a setext-style rule under text is not mistaken for a table", () => {
  // No pipe on the first line, so it must not open a table.
  const root = render(["Some heading", "------------"].join("\n"));

  assert.equal(root.find("table").length, 0);
});

test("text before and after a table survives", () => {
  const root = render(["Before.", "", TABLE, "", "After."].join("\n"));

  assert.equal(root.find("table").length, 1);
  const text = root.textContent;
  assert.ok(text.includes("Before."));
  assert.ok(text.includes("After."));
});

// --- the rest of the renderer, guarding the loop rewrite ----------------------
//
// Converting the block loop to an indexed one for table lookahead could have
// broken any other branch, so each keeps a witness.

test("headings, lists, quotes and fences still render", () => {
  const root = render(
    ["# Title", "- one", "- two", "> quoted", "```js", "code()", "```"].join("\n"),
  );

  assert.equal(root.find("h3").length, 1);
  assert.equal(root.find("ul").length, 1);
  assert.equal(root.find("li").length, 2);
  assert.equal(root.find("blockquote").length, 1);
  assert.equal(root.find("pre").length, 1);
});

test("a nested list keeps its parent intact", () => {
  const root = render(["1. step", "   - detail", "2. next"].join("\n"));

  assert.equal(root.find("ol").length, 1);
  assert.equal(root.find("ol")[0].find("li").length, 3);
});

test("an unterminated fence still renders as code", () => {
  // Normal mid-stream: the closing fence has not arrived yet.
  const root = render(["```", "half a block"].join("\n"));

  assert.equal(root.find("pre").length, 1);
});

test("a non-http link is left as text", () => {
  const root = render("[click](javascript:alert(1))");

  assert.equal(root.find("a").length, 0);
});

// --- citation highlighting: block-granularity, not character-exact ------------
//
// A character-exact <mark> around the cited substring — the way plain text is
// highlighted — is not available through a markdown parse: splitting the raw
// source mid-syntax would corrupt everything after the cut. These pin the
// fallback instead: whichever rendered block a cited line range touches gets
// marked, for every block type the renderer produces.

import { highlighted } from "./dom.mjs";

test("a paragraph touching the highlighted range is marked", () => {
  const root = render("First paragraph.\n\nSecond paragraph.", { start: 0, end: 0 });

  const marked = highlighted(root);
  assert.equal(marked.length, 1);
  assert.equal(marked[0].tagName, "p");
  assert.ok(marked[0].textContent.includes("First"));
});

test("a paragraph outside the range is not marked", () => {
  const root = render("First paragraph.\n\nSecond paragraph.", { start: 0, end: 0 });

  const text = highlighted(root).map((el) => el.textContent).join("");
  assert.ok(!text.includes("Second"));
});

test("a heading on the cited line is marked", () => {
  const root = render("# Title\n\nBody.", { start: 0, end: 0 });

  const [marked] = highlighted(root);
  assert.equal(marked.tagName, "h3");
});

test("a list item on the cited line is marked, not the whole list", () => {
  const root = render("- one\n- two\n- three", { start: 1, end: 1 });

  const marked = highlighted(root);
  assert.equal(marked.length, 1);
  assert.equal(marked[0].tagName, "li");
  assert.equal(marked[0].textContent, "two");
});

test("an indented continuation line marks its own item", () => {
  const root = render("- step one\n  detail line", { start: 1, end: 1 });

  const marked = highlighted(root);
  // The continuation becomes its own nested <li> under the same list — see
  // createListBuilder — so it, not the parent step, is what carries the class.
  assert.equal(marked.length, 1);
  assert.equal(marked[0].textContent, "detail line");
});

test("a blockquote on the cited line is marked", () => {
  const root = render("> quoted line", { start: 0, end: 0 });

  const [marked] = highlighted(root);
  assert.equal(marked.tagName, "blockquote");
});

test("a fenced code block is marked when the range falls inside the fence", () => {
  const root = render(["```", "line one", "line two", "```"].join("\n"), {
    start: 2,
    end: 2,
  });

  const [marked] = highlighted(root);
  assert.equal(marked.tagName, "pre");
});

test("an unterminated fence at end of stream can still be marked", () => {
  const root = render(["```", "half a block"].join("\n"), { start: 1, end: 1 });

  const [marked] = highlighted(root);
  assert.equal(marked.tagName, "pre");
});

test("a table is marked as a whole when the range touches any of its rows", () => {
  const table = ["| a | b |", "|---|---|", "| 1 | 2 |", "| 3 | 4 |"].join("\n");
  const root = render(table, { start: 3, end: 3 });

  const [marked] = highlighted(root);
  assert.equal(marked.tagName, "div"); // the .md__table-wrap
  assert.ok(marked.find("table").length === 1);
});

test("a range spanning two blocks marks both", () => {
  const root = render("First paragraph.\n\nSecond paragraph.", { start: 0, end: 2 });

  assert.equal(highlighted(root).length, 2);
});

test("no highlightLines argument marks nothing, matching the old default", () => {
  const root = render("First paragraph.\n\nSecond paragraph.");

  assert.equal(highlighted(root).length, 0);
});

test("a range outside every line marks nothing", () => {
  const root = render("only one paragraph here", { start: 50, end: 60 });

  assert.equal(highlighted(root).length, 0);
});
