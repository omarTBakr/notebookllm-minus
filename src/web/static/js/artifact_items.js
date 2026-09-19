// The decisions a generated set makes about its own items, with no DOM and no
// imports.
//
// Separated for the same reason markdown.js is: a module with no dependencies
// can be loaded under `node --test` by reading and evaluating it, so the rules
// that decide whether a card can be cited or how a quiz is scored are testable
// without a browser, jsdom, or a package.json in the directory the app serves.
//
// Everything here is pure. The panels hold the state; this decides what it
// means.

/** True when an item can be traced back to a page.
 *
 * Both halves are required. `chunk_order` counts within one *document*, so a
 * notebook holding two files has two chunk 5s — the asset is what says which,
 * and an item carrying only one of the two cannot be opened at all.
 *
 * `chunk_order` is checked against null and undefined rather than falsiness:
 * chunk 0 is the first chunk of a document, and a truthiness test would make
 * the opening page of every file uncitable.
 */
export function citable(item) {
  return Boolean(
    item &&
      item.asset_id &&
      item.chunk_order !== undefined &&
      item.chunk_order !== null,
  );
}

/** How many answers were right.
 *
 * `answers` maps a question index to the option index that was picked. Scored
 * against the questions currently held rather than against what was asked at
 * the time, because a set grows while it is being taken — but an answer to a
 * question that has since disappeared counts for nothing rather than throwing.
 */
export function score(questions, answers) {
  let right = 0;

  for (const [at, picked] of answers) {
    const question = questions[at];

    if (question && picked === question.answer_index) right += 1;
  }

  return right;
}

/** Keep a position inside a list that is still growing.
 *
 * A set is appended to while it is read, so the index has to survive the list
 * changing under it. Growing must not move the reader — that is the whole
 * point of holding a position rather than deriving one — while shrinking, which
 * only happens on a regeneration, pulls them back to the last item that exists.
 */
export function clampIndex(index, length) {
  if (length <= 0) return 0;

  return Math.min(Math.max(0, index), length - 1);
}

/** The label under a card, given whether it is showing its answer. */
export const cardSide = (flipped) => (flipped ? "back" : "front");

/** A mind map's items as branches, in the order the server gave them.
 *
 * Items carry `branch` once the outline pass has run. Until then — the map is
 * still generating — they have none, and come back as one branch titled "" so
 * the panel can draw them straight under the root rather than hiding them.
 */
export function mindMapBranches(items) {
  const branches = [];
  const at = new Map();

  for (const item of items ?? []) {
    if (!item) continue;

    const title = item.branch ?? "";

    if (!at.has(title)) {
      at.set(title, branches.length);
      branches.push({ title, nodes: [] });
    }

    branches[at.get(title)].nodes.push(item);
  }

  return branches;
}

/** Where every node of a mind map goes, given how big each one turned out.
 *
 * Left to right: the root, then a column of branches, then a column of topics.
 * Each branch sits level with the middle of its own topics, and the root level
 * with the middle of everything. A branch with no title (topics not grouped
 * yet) takes no room of its own: its topics hang straight off the root. A
 * collapsed branch keeps its place and drops its topics.
 *
 * Sizes are measured by the caller, so this is arithmetic only — which is what
 * lets it be tested without a browser. Mirroring for right-to-left is left to
 * the caller too: it is one subtraction per x, at draw time.
 */
export function layoutMindMap(tree, { gapX = 56, gapY = 10, branchGap = 22 } = {}) {
  const branches = tree?.branches ?? [];
  const root = tree?.root ?? { w: 0, h: 0 };

  const branchX = root.w + gapX;
  const branchColumn = Math.max(0, ...branches.map((b) => b.w || 0));
  const topicX = branchX + (branchColumn ? branchColumn + gapX : 0);

  const placed = [];
  let y = 0;

  for (const branch of branches) {
    const shown = branch.collapsed ? [] : branch.topics ?? [];
    const stack = shown.reduce((sum, t) => sum + t.h, 0) + gapY * Math.max(0, shown.length - 1);
    const block = Math.max(branch.h || 0, stack);

    let at = y + (block - stack) / 2;
    const topics = shown.map((t) => {
      const box = { x: topicX, y: at, w: t.w, h: t.h };
      at += t.h + gapY;
      return box;
    });

    placed.push({ x: branchX, y: y + (block - (branch.h || 0)) / 2, w: branch.w || 0, h: branch.h || 0, topics });
    y += block + branchGap;
  }

  const height = Math.max(root.h, placed.length ? y - branchGap : 0);
  const width = Math.max(
    root.w,
    ...placed.flatMap((b) => [b.x + b.w, ...b.topics.map((t) => t.x + t.w)]),
  );

  return {
    root: { x: 0, y: (height - root.h) / 2, w: root.w, h: root.h },
    branches: placed,
    width,
    height,
  };
}

/** A curved link from the end of one box to the start of the next. */
export function linkPath(from, to) {
  const x1 = from.x + from.w;
  const y1 = from.y + from.h / 2;
  const x2 = to.x;
  const y2 = to.y + to.h / 2;
  const mid = (x1 + x2) / 2;

  return `M ${x1} ${y1} C ${mid} ${y1}, ${mid} ${y2}, ${x2} ${y2}`;
}
