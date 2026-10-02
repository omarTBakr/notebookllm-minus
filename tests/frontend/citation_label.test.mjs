// How a citation's link reads: page, video moment, or spreadsheet row.
//
//     node --test tests/frontend/*.test.mjs

import test from "node:test";
import assert from "node:assert/strict";

import { citationLabel, citationTitle } from "../../src/presentation/web/static/js/citation_label.js";

const t = (key) => ({ row: "row", page: "p.", openAtRow: "row!", openAtPage: "page!", openSource: "src!", openAtMoment: "time!" })[key];

test("a CSV row is labelled with its row number", () => {
  assert.equal(citationLabel({ source: "a.csv", row: 12 }, t), "a.csv · row 12");
});

test("an XLSX row names its sheet", () => {
  assert.equal(citationLabel({ source: "b.xlsx", sheet: "Sales", row: 2 }, t), "b.xlsx · Sales · row 2");
});

test("pages, video moments and plain sources keep their labels", () => {
  assert.equal(citationLabel({ source: "r.pdf", page_label: "4" }, t), "r.pdf · p. 4");
  assert.equal(citationLabel({ source: "v", time_label: "1:02" }, t), "v · 1:02");
  assert.equal(citationLabel({ source: "n.txt" }, t), "n.txt");
});

test("tooltips say what the click does", () => {
  assert.equal(citationTitle({ row: 3 }, t), "row!");
  assert.equal(citationTitle({ page_number: 2 }, t), "page!");
  assert.equal(citationTitle({}, t), "src!");
});
