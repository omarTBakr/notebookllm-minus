// Shared plumbing for the Studio sets: start one, and keep reading it while
// it fills.
//
// Flashcards and quizzes differ only in how an item is drawn. Starting a run,
// polling it, appending what arrived and knowing when to stop are identical,
// so they live here once and each panel supplies a renderer.
//
// The polling is the point. A 694-chunk book takes minutes, and the server
// appends items batch by batch precisely so the browser can show a partial set
// — waiting for the whole thing was the blank screen this replaced.

import { api } from "./api.js";
import { citable } from "./artifact_items.js";
import { t } from "./i18n.js";
import { toast } from "./soon.js";
import { openAt } from "./sources.js";
import { state } from "./state.js";

// Slow enough not to hammer the API for minutes, quick enough that a batch
// landing feels immediate. A batch takes seconds, so anything much longer than
// this shows nothing new most of the time.
const POLL_MS = 2000;

// Kinds still filling, so a second click on the tile does not start a second
// poller against the same set. Keyed by `${chatId}:${kind}` because the panel
// survives a notebook switch.
const polling = new Set();

/** Start a generation, then poll until it stops growing.
 *
 * `onItems` is called with the full item list every time it changes, including
 * the first partial batch — the renderer redraws from scratch rather than
 * being handed a delta, which keeps it honest about what the server actually
 * holds.
 */
export async function generate(kind, { onItems, onStatus }) {
  const chatId = state.notebook?.chat_id;

  // A click must always produce a visible result. This used to quietly return
  // before a notebook had loaded, which made both Studio actions look broken.
  if (!chatId) {
    toast(t("noNotebookYet"));
    return;
  }

  const key = `${chatId}:${kind}`;

  if (polling.has(key)) {
    toast(t("studioAlreadyRunning"));
    return;
  }

  polling.add(key);
  onStatus?.("generating", 0);

  try {
    await api.generateArtifact(chatId, kind);
  } catch (err) {
    polling.delete(key);
    onStatus?.("failed", 0);

    // The server says *why* when it can — "add a document and let it finish
    // indexing first" is worth reading, and is the common case: a tile
    // clicked while the upload is still going.
    toast(err?.detail || err?.message || t("studioFailed"));
    return;
  }

  await poll(chatId, kind, { onItems, onStatus });
}

async function poll(chatId, kind, { onItems, onStatus }) {
  const key = `${chatId}:${kind}`;

  try {
    for (;;) {
      // The notebook can be switched while a set is generating. Its task keeps
      // running server-side — the artifact is stored, not held in this page —
      // but this poller stops, or it would repaint a panel showing something
      // else entirely.
      if (state.notebook?.chat_id !== chatId) return;

      let set;

      try {
        set = await api.readArtifact(chatId, kind);
      } catch {
        // A transient read failure is not a failed generation: the task is
        // still going. Try again rather than tearing the panel down.
        await sleep(POLL_MS);
        continue;
      }

      // `exists: false` is not "still working". The row is created by the
      // route before the task is queued, so once a POST has returned, an
      // artifact that is not there is one that will never be there — the
      // notebook was deleted, or the row was swept. Defaulting this to
      // "generating" is what left the panel saying "reading the documents…"
      // for four minutes after the task behind it had already died.
      if (set.exists === false) {
        onStatus?.("failed", 0);
        toast(t("studioFailed"));
        return;
      }

      onItems?.(set.items ?? []);
      onStatus?.(set.status ?? "generating", set.count ?? 0);

      if (set.status === "complete" || set.status === "failed") {
        if (set.status === "failed" && !(set.items ?? []).length) {
          toast(t("studioFailed"));
        }
        return;
      }

      await sleep(POLL_MS);
    }
  } finally {
    polling.delete(key);
  }
}

/** Read a set once, without starting anything.
 *
 * Called when the panel opens, so an existing deck appears immediately rather
 * than only after someone asks for it again. If one is still generating,
 * polling resumes — a reload mid-run must not leave the set frozen at whatever
 * it had reached, which is the bug the upload progress bar had.
 */
export async function resume(kind, { onItems, onStatus }) {
  const chatId = state.notebook?.chat_id;

  if (!chatId) return null;

  let set;

  try {
    set = await api.readArtifact(chatId, kind);
  } catch {
    return null;
  }

  if (!set.exists) return null;

  onItems?.(set.items ?? []);
  onStatus?.(set.status ?? "complete", set.count ?? 0);

  if (set.status === "generating" && !polling.has(`${chatId}:${kind}`)) {
    polling.add(`${chatId}:${kind}`);
    poll(chatId, kind, { onItems, onStatus });
  }

  return set;
}

/** Give the whole Studio panel over to one artifact, or hand it back.
 *
 * Shared rather than duplicated in both panels, because they must agree: two
 * copies of this would eventually disagree about whether the grid is showing,
 * and the panel would end up with a deck and nine tiles stacked in one narrow
 * column — which is the layout this replaces.
 *
 * The class goes on the panel, not the section, so the CSS can hide the grid,
 * the empty state and the note button in one rule. `hidden` on the sections
 * still decides which of the two is up.
 */
export function takeOverPanel(on) {
  document.getElementById("panel-studio")?.classList.toggle("is-artifact", on);
}

// Re-exported so a panel imports one module rather than two for the same idea.
export { citable };

/** Open the document an item came from, at the page it came from.
 *
 * The page has to be looked up first. A chat citation carries `page_number`
 * because it was stored with the answer; a generated item carries only the
 * chunk, and `openAt` draws its highlight *only* when given both a page and a
 * chunk. Passing null for the page — which this did — meant every card and
 * question opened the document at the top with nothing marked, which is the
 * bare minimum a citation should not be.
 *
 * `/locate` is the same endpoint the PDF highlighter already calls, so this
 * asks it directly rather than inventing a second way to find a chunk.
 *
 * A failed lookup still opens the document. Losing the highlight is a much
 * smaller loss than refusing to open the source at all, and a TEXT or
 * MARKDOWN chunk has no page number to find in the first place.
 */
export async function cite(item) {
  if (!citable(item)) return;

  const chatId = state.notebook?.chat_id;
  const source = state.sources?.find((s) => s.asset_id === item.asset_id);
  let page = null;

  // Only a PDF needs this. A TEXT or MARKDOWN source has no page number at
  // all, and its viewer resolves its own highlight from `text_range` — asking
  // here too would be a second identical request per citation click for an
  // answer that is always null.
  if (chatId && source?.asset_type === "pdf") {
    try {
      const located = await api.locateChunk(chatId, item.asset_id, item.chunk_order);
      page = located?.page_number ?? null;
    } catch {
      // Falls through with page = null: the document still opens, just
      // without the highlight.
    }
  }

  await openAt(item.asset_id, page, item.chunk_order);
}

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
