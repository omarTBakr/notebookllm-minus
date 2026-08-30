// Just enough DOM to run the markdown renderer under `node --test`.
//
// The renderer builds nodes rather than HTML strings, which is what makes it
// safe — and also what makes it untestable without a document. Rather than
// pull in jsdom (the project has no node_modules and no package.json, and
// keeping it that way is worth more than the convenience), this implements the
// handful of methods markdown.js actually calls.
//
// Loading is done by reading the source and evaluating it: markdown.js has no
// imports of its own, so it needs nothing but a `document` in scope. That
// avoids having to declare the static directory an ES module package, which
// would put a package.json inside the folder the app serves to browsers.

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

class ClassList {
  constructor() {
    this._set = new Set();
  }
  add(name) { this._set.add(name); }
  remove(name) { this._set.delete(name); }
  contains(name) { return this._set.has(name); }
}

class Node {
  constructor(tag) {
    this.tagName = tag;
    this.children = [];
    this.style = {};
    this.dataset = {};
    this.classList = new ClassList();
    this._text = "";
  }

  append(...nodes) {
    for (const node of nodes) this.children.push(node);
  }

  set textContent(value) {
    this._text = String(value);
    this.children = [];
  }

  /** Concatenated text of this node and everything under it. */
  get textContent() {
    if (this.children.length === 0) return this._text;
    return this.children.map((child) => child.textContent).join("");
  }

  /** Every descendant with the given tag name, in document order. */
  find(tag) {
    const out = [];
    for (const child of this.children) {
      if (child.tagName === tag) out.push(child);
      if (child.find) out.push(...child.find(tag));
    }
    return out;
  }
}

class TextNode {
  constructor(text) {
    this.tagName = "#text";
    this._text = String(text);
    this.children = [];
  }
  get textContent() {
    return this._text;
  }
  find() {
    return [];
  }
}

export const document = {
  createElement: (tag) => new Node(tag),
  createTextNode: (text) => new TextNode(text),
  createDocumentFragment: () => new Node("#fragment"),
};

const here = dirname(fileURLToPath(import.meta.url));
const source = readFileSync(
  resolve(here, "../../src/web/static/js/markdown.js"),
  "utf8",
);

// `export` is meaningless inside a Function body; the names are handed back
// explicitly instead.
const body = source.replace(/^export /gm, "");

export const { renderMarkdown, renderInto } = new Function(
  "document",
  `${body}\n return { renderMarkdown, renderInto };`,
)(document);

/** The rendered tree for *markdown*, as a queryable root node.
 *
 * *highlightLines*, when given, is forwarded to renderMarkdown as its
 * citation-highlight range — see markdown.js for what it does.
 */
export function render(markdown, highlightLines = null) {
  return renderMarkdown(markdown, highlightLines);
}

/** Every element anywhere in *root* carrying the cite-highlight class. */
export function highlighted(root) {
  const out = [];
  const walk = (node) => {
    if (node.classList?.contains("cite-highlight")) out.push(node);
    for (const child of node.children ?? []) walk(child);
  };
  walk(root);
  return out;
}

/** Row-by-row text of the first table, header first. */
export function tableText(root) {
  const [table] = root.find("table");
  if (!table) return null;

  return [
    table.find("th").map((cell) => cell.textContent),
    ...table
      .find("tr")
      .filter((row) => row.find("td").length)
      .map((row) => row.find("td").map((cell) => cell.textContent)),
  ];
}
