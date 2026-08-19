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
  // Each level: { list, ordered, indent, lastItem }
  const stack = [];

  return {
    add(indent, ordered, text) {
      // Leave any level indented deeper than this line.
      while (stack.length && indent < stack[stack.length - 1].indent) stack.pop();

      let top = stack[stack.length - 1];

      const deeper = top && indent > top.indent;
      const switched = top && !deeper && ordered !== top.ordered;

      if (!top || deeper || switched) {
        const list = document.createElement(ordered ? "ol" : "ul");

        if (deeper && top.lastItem) {
          // A nested list belongs inside the item it sits under.
          top.lastItem.append(list);
        } else {
          if (switched) stack.pop();
          (stack.length ? stack[stack.length - 1].lastItem : fragment).append(list);
        }

        top = { list, ordered, indent, lastItem: null };
        stack.push(top);
      }

      const li = document.createElement("li");
      li.append(...inlineNodes(text));
      top.list.append(li);
      top.lastItem = li;
    },

    flush() {
      stack.length = 0;
    },

    get active() {
      return stack.length > 0;
    },
  };
}

function flushParagraph(fragment, lines) {
  if (!lines.length) return;

  const p = document.createElement("p");
  // A single newline inside a paragraph is a soft break, as in chat UIs.
  lines.join("\n").split("\n").forEach((line, index) => {
    if (index) p.append(document.createElement("br"));
    p.append(...inlineNodes(line));
  });

  fragment.append(p);
  lines.length = 0;
}

/** Render *text* as Markdown into a DocumentFragment. */
export function renderMarkdown(text) {
  const fragment = document.createDocumentFragment();
  const lines = String(text ?? "").split("\n");

  let paragraph = [];
  let codeLines = null;
  let codeFence = "";

  const lists = createListBuilder(fragment);

  const flushAll = () => {
    flushParagraph(fragment, paragraph);
    lists.flush();
  };

  for (const line of lines) {
    const fence = /^\s*```(.*)$/.exec(line);

    if (fence) {
      if (codeLines === null) {
        flushAll();
        codeLines = [];
        codeFence = fence[1].trim();
      } else {
        const pre = document.createElement("pre");
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

    if (heading) {
      flushAll();
      const el = document.createElement(`h${Math.min(heading[1].length + 2, 6)}`);
      el.append(...inlineNodes(heading[2]));
      fragment.append(el);
    } else if (bullet || numbered) {
      flushParagraph(fragment, paragraph);
      const match = bullet ?? numbered;
      // Tabs count as four columns, matching how the text was written.
      const indent = match[1].replace(/\t/g, "    ").length;
      lists.add(indent, Boolean(numbered), match[2]);
    } else if (quote) {
      flushAll();
      const el = document.createElement("blockquote");
      el.append(...inlineNodes(quote[1]));
      fragment.append(el);
    } else if (!line.trim()) {
      flushAll();
    } else if (lists.active && /^\s{2,}\S/.test(line)) {
      // An indented continuation line belongs to the item above it, not to a
      // new paragraph that would break the list apart.
      lists.add(999, false, line.trim());
    } else {
      lists.flush();
      paragraph.push(line);
    }
  }

  // An unterminated fence is normal mid-stream: show it as code anyway.
  if (codeLines !== null) {
    const pre = document.createElement("pre");
    const code = document.createElement("code");
    code.textContent = codeLines.join("\n");
    pre.append(code);
    fragment.append(pre);
  }

  flushAll();

  return fragment;
}

/** Replace *el*'s children with the rendered Markdown of *text*. */
export function renderInto(el, text) {
  el.replaceChildren(renderMarkdown(text));
}
