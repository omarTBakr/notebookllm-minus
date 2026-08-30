// A small Markdown renderer that builds DOM nodes rather than HTML strings.
//
// Why not innerHTML: the text being rendered comes from a model that is in
// turn repeating the user's own documents. Assembling markup from it would
// make any `<script>` in an uploaded PDF executable in the page. Every leaf
// here goes through textContent, so the worst a document can do is look odd.
//
// Deliberately partial — headings, lists, code, quotes, bold/italic/code
// spans and links. That covers what chat models actually emit.

const INLINE = /(\*\*[^*]+\*\*|__[^_]+__|\*[^*\n]+\*|_[^_\n]+_|`[^`]+`|\[[^\]]+\]\([^)\s]+\))/g;

function inlineNodes(text) {
  const out = [];

  for (const part of text.split(INLINE)) {
    if (!part) continue;

    let match;

    if ((part.startsWith("**") && part.endsWith("**")) || (part.startsWith("__") && part.endsWith("__"))) {
      const el = document.createElement("strong");
      el.textContent = part.slice(2, -2);
      out.push(el);
    } else if ((part.startsWith("*") && part.endsWith("*")) || (part.startsWith("_") && part.endsWith("_"))) {
      const el = document.createElement("em");
      el.textContent = part.slice(1, -1);
      out.push(el);
    } else if (part.startsWith("`") && part.endsWith("`")) {
      const el = document.createElement("code");
      el.textContent = part.slice(1, -1);
      out.push(el);
    } else if ((match = /^\[([^\]]+)\]\(([^)\s]+)\)$/.exec(part))) {
      // Only http(s). A javascript: or data: href would be an XSS vector.
      const [, label, href] = match;
      if (/^https?:\/\//i.test(href)) {
        const el = document.createElement("a");
        el.href = href;
        el.textContent = label;
        el.target = "_blank";
        el.rel = "noopener noreferrer";
        out.push(el);
      } else {
        out.push(document.createTextNode(part));
      }
    } else {
      out.push(document.createTextNode(part));
    }
  }

  return out;
}

/** Builds nested lists from indented markers.
 *
 * A flat "close this list, open another" approach breaks a numbered list in
 * half whenever a sub-bullet appears under one of its steps, restarting the
 * numbering at 1. Tracking indentation keeps the outer list intact and hangs
 * the sub-items inside the step they belong to.
 */
function createListBuilder(fragment) {
  const stack = [];

  return {
    add(indent, ordered, text, touched = false) {
      while (stack.length && indent < stack[stack.length - 1].indent) stack.pop();

      let top = stack[stack.length - 1];

      const deeper = top && indent > top.indent;
      const switched = top && !deeper && ordered !== top.ordered;

      if (!top || deeper || switched) {
        const list = document.createElement(ordered ? "ol" : "ul");

        if (deeper && top.lastItem) {
          top.lastItem.append(list);
        } else {
          if (switched) stack.pop();
          (stack.length ? stack[stack.length - 1].lastItem : fragment).append(list);
        }

        top = { list, ordered, indent, lastItem: null };
        stack.push(top);
      }

      const li = document.createElement("li");
      // An indented continuation line calls add() again against the same
      // item's own sub-list rather than the item itself, so a citation
      // touching only a continuation line still needs the class here, not
      // only on whichever line first opened the <li>.
      if (touched) li.classList.add("cite-highlight");
      li.append(...inlineNodes(text));
      top.list.append(li);
      top.lastItem = li;
    },

    flush() { stack.length = 0; },
    get active() { return stack.length > 0; },
  };
}

// A separator row: the line under a table's header. At least one dash per
// cell, optional colons for alignment, optional outer pipes. This is the line
// that decides a run of pipes is a table at all, which is why it must be
// recognised exactly — `| not | a table |` on its own is a paragraph.
const TABLE_RULE = /^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?\s*$/;

/** Cells of one row, with the optional outer pipes removed.
 *
 * Splits on unescaped pipes only, so `\|` can appear inside a cell.
 */
function tableCells(line) {
  const trimmed = line.trim().replace(/^\|/, "").replace(/\|$/, "");

  return trimmed
    .split(/(?<!\\)\|/)
    .map((cell) => cell.trim().replace(/\\\|/g, "|"));
}

/** "left" | "center" | "right" | null, per column, from the separator row. */
function tableAlignment(rule) {
  return tableCells(rule).map((cell) => {
    const start = cell.startsWith(":");
    const end = cell.endsWith(":");

    if (start && end) return "center";
    if (end) return "right";
    if (start) return "left";
    return null;
  });
}

/** True when *line* could open a table and *next* confirms it.
 *
 * Both halves are required. A header alone is just text — which is also what
 * makes this safe mid-stream: a table arriving token by token renders as a
 * paragraph until its separator lands, then becomes a table, exactly the way
 * a list does.
 */
function opensTable(line, next) {
  return (
    line !== undefined &&
    next !== undefined &&
    line.includes("|") &&
    TABLE_RULE.test(next) &&
    next.includes("-")
  );
}

/** Build the table, returning it with the index of the last line consumed. */
function buildTable(lines, start) {
  const align = tableAlignment(lines[start + 1]);
  const headers = tableCells(lines[start]);

  const table = document.createElement("table");
  const thead = document.createElement("thead");
  const headRow = document.createElement("tr");

  headers.forEach((text, column) => {
    const th = document.createElement("th");
    // Per cell, not per line. Running the inline pass over the whole row would
    // let an emphasis marker in one cell pair with one in the next and
    // italicise straight across the pipe between them.
    th.append(...inlineNodes(text));
    if (align[column]) th.style.textAlign = align[column];
    headRow.append(th);
  });

  thead.append(headRow);
  table.append(thead);

  const tbody = document.createElement("tbody");
  let index = start + 2;

  for (; index < lines.length; index += 1) {
    const line = lines[index];
    // A blank line, or anything without a pipe, ends the table.
    if (!line.trim() || !line.includes("|")) break;

    const row = document.createElement("tr");
    const cells = tableCells(line);

    // Ragged rows are normal in model output. Pad to the header width rather
    // than dropping the row, so a short row loses nothing.
    for (let column = 0; column < headers.length; column += 1) {
      const td = document.createElement("td");
      td.append(...inlineNodes(cells[column] ?? ""));
      if (align[column]) td.style.textAlign = align[column];
      row.append(td);
    }

    tbody.append(row);
  }

  table.append(tbody);

  // Wrapped, because a wide table must scroll inside the message rather than
  // widening it — .msg is capped at 760px.
  const wrap = document.createElement("div");
  wrap.className = "md__table-wrap";
  wrap.append(table);

  return { node: wrap, next: index - 1 };
}

function flushParagraph(fragment, lines, touched) {
  if (!lines.length) return;

  const p = document.createElement("p");
  if (touched) p.classList.add("cite-highlight");

  lines.join("\n").split("\n").forEach((line, index) => {
    if (index) p.append(document.createElement("br"));
    p.append(...inlineNodes(line));
  });

  fragment.append(p);
  lines.length = 0;
}

/** Render *text* as Markdown into a DocumentFragment.
 *
 * *highlightLines*, when given, is a `{start, end}` pair of 0-based,
 * inclusive line indices — a citation's cited range, translated from a
 * character offset into a line range before this is called (see
 * lineRangeFor in sources.js). Whichever rendered block(s) that range
 * touches get a `cite-highlight` class, for the caller to colour.
 *
 * A character-exact `<mark>` around the cited substring — the way plain
 * text is highlighted — is not available here: splitting the raw markdown
 * mid-syntax (`**bo<mark>ld**` closing inside a mark, or worse, inside
 * `**`) would corrupt the parse for everything after the cut. Block
 * granularity is what survives parsing the text as markdown at all.
 */
export function renderMarkdown(text, highlightLines = null) {
  const fragment = document.createDocumentFragment();
  const lines = String(text ?? "").split("\n");

  const inRange = (index) =>
    highlightLines !== null && index >= highlightLines.start && index <= highlightLines.end;
  const rangeOverlaps = (start, end) =>
    highlightLines !== null && start <= highlightLines.end && end >= highlightLines.start;

  let paragraph = [];
  let paragraphTouched = false;
  let codeLines = null;
  let codeFence = "";
  let codeStart = -1;

  const lists = createListBuilder(fragment);

  const flushAll = () => {
    flushParagraph(fragment, paragraph, paragraphTouched);
    paragraphTouched = false;
    lists.flush();
  };

  // Indexed rather than for-of: a table is only a table if the line *after*
  // its header is a separator row, so the loop needs one line of lookahead.
  for (let index = 0; index < lines.length; index += 1) {
    const line = lines[index];
    const fence = /^\s*```(.*)$/.exec(line);

    if (fence) {
      if (codeLines === null) {
        flushAll();
        codeLines = [];
        codeFence = fence[1].trim();
        codeStart = index;
      } else {
        const pre = document.createElement("pre");
        if (rangeOverlaps(codeStart, index)) pre.classList.add("cite-highlight");
        const code = document.createElement("code");
        if (codeFence) code.dataset.lang = codeFence;
        code.textContent = codeLines.join("\n");
        pre.append(code);
        fragment.append(pre);
        codeLines = null;
      }
      continue;
    }

    // Inside a fence everything is literal, including what looks like syntax.
    if (codeLines !== null) {
      codeLines.push(line);
      continue;
    }

    const heading = /^(#{1,6})\s+(.*)$/.exec(line);
    const bullet = /^(\s*)[-*+]\s+(.*)$/.exec(line);
    const numbered = /^(\s*)\d+[.)]\s+(.*)$/.exec(line);
    const quote = /^\s*>\s?(.*)$/.exec(line);

    if (opensTable(line, lines[index + 1])) {
      flushAll();
      const { node, next } = buildTable(lines, index);
      if (rangeOverlaps(index, next)) node.classList.add("cite-highlight");
      fragment.append(node);
      index = next;
    } else if (heading) {
      flushAll();
      const el = document.createElement(`h${Math.min(heading[1].length + 2, 6)}`);
      if (inRange(index)) el.classList.add("cite-highlight");
      el.append(...inlineNodes(heading[2]));
      fragment.append(el);
    } else if (bullet || numbered) {
      flushParagraph(fragment, paragraph, paragraphTouched);
      paragraphTouched = false;
      const match = bullet ?? numbered;
      // Tabs count as four columns, matching how the text was written.
      const indent = match[1].replace(/\t/g, "    ").length;
      lists.add(indent, Boolean(numbered), match[2], inRange(index));
    } else if (quote) {
      flushAll();
      const el = document.createElement("blockquote");
      if (inRange(index)) el.classList.add("cite-highlight");
      el.append(...inlineNodes(quote[1]));
      fragment.append(el);
    } else if (!line.trim()) {
      flushAll();
    } else if (lists.active && /^\s{2,}\S/.test(line)) {
      // An indented continuation line belongs to the item above it, not to a
      // new paragraph that would break the list apart.
      lists.add(999, false, line.trim(), inRange(index));
    } else {
      lists.flush();
      paragraph.push(line);
      if (inRange(index)) paragraphTouched = true;
    }
  }

  // An unterminated fence is normal mid-stream: show it as code anyway.
  if (codeLines !== null) {
    const pre = document.createElement("pre");
    if (rangeOverlaps(codeStart, lines.length - 1)) pre.classList.add("cite-highlight");
    const code = document.createElement("code");
    code.textContent = codeLines.join("\n");
    pre.append(code);
    fragment.append(pre);
  }

  flushAll();

  return fragment;
}

/** Replace *el*'s children with the rendered Markdown of *text*. */
export function renderInto(el, text, highlightLines = null) {
  el.replaceChildren(renderMarkdown(text, highlightLines));
}
