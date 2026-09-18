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
