// `/memory`: an autocomplete suggestion in the composer, a confirmation
// before it's actually sent, and picking up the worker's reply once it lands.
//
// The backend answers a `/memory` message with an immediate ack and finishes
// the real work in a background task (see routes/chat/_memory.py and
// tasks/jobs/memory.py), which writes the real answer as a second assistant
// message once it's done. chat.js and app.js stay unaware any of that is
// happening — they render two ordinary assistant replies, a little apart.

import { api } from "./api.js";
import { memoryFactItem, renderMessage } from "./chat.js";
import { confirmDialog } from "./dialog.js";
import { $ } from "./dom.js";
import { t } from "./i18n.js";
import { toast } from "./soon.js";
import { state } from "./state.js";

const COMMAND = "/memory";

/** The text after `/memory`, or null if *text* is not that command. */
export function isMemoryCommand(text) {
  const stripped = text.trim();
  if (!stripped.toLowerCase().startsWith(COMMAND)) return null;
  return stripped.slice(COMMAND.length).trim();
}

/** Ask the user to confirm before a `/memory` message is actually sent.
 * Resolves true only if they chose Save.
 */
export function confirmMemorySave(remainder) {
  return confirmDialog({
    title: t("memoryConfirmTitle"),
    message: remainder || t("memoryConfirmEmpty"),
    confirm: t("save"),
  });
}

// --- autocomplete suggestion -------------------------------------------------

/** True once "/memory" itself has been completed and cleared out of the
 * box — the chip alone marks the mode from here, and the box holds only
 * the fact being typed, not the command that put it there. Module-level
 * rather than a bindMemorySuggestion() local: composeMemoryText and
 * isMemoryActive below need it too, and both are called from app.js.
 */
let active = false;

/** A strict, not-yet-complete prefix of "/memory" — the only state where
 * Tab/Enter should *complete* the command rather than do their ordinary
 * job.
 */
function shouldSuggest(text) {
  return text.length > 0 && text.length < COMMAND.length && COMMAND.startsWith(text.toLowerCase());
}

/** Whether the current draft is a `/memory` message, active mode or not —
 * what app.js checks instead of calling isMemoryCommand directly, since
 * once active the box's own text no longer carries the word "memory" for
 * that to find.
 */
export function isMemoryActive() {
  return active;
}

/** The text to actually send: the "/memory" prefix reconstructed if active
 * mode stripped it from the box, otherwise *text* unchanged. app.js calls
 * this in place of the raw box value once a send is confirmed.
 */
export function composeMemoryText(text) {
  return active ? `${COMMAND} ${text}`.trim() : text;
}

/** Leave `/memory` mode and hide the chip — called from app.js once a
 * `/memory` message actually sends. Also the chip's own doing, on Backspace
 * against an empty box (see the keydown handler below).
 */
export function resetMemoryMode() {
  active = false;
  const panel = $("memory-suggest");
  if (panel) panel.hidden = true;
}

/** Wire the "/memory" suggestion chip in the composer row. A no-op if the
 * chat panel's markup does not have it (e.g. a page that never shows chat).
 */
export function bindMemorySuggestion() {
  const box = $("question");
  const panel = $("memory-suggest");
  const item = $("memory-suggest-item");

  if (!box || !panel || !item) return;

  const accept = () => {
    if (active) return; // nothing left to accept once already in the mode
    box.value = `${COMMAND} `;
    // autoGrow listens for this; dispatched rather than called directly so
    // this module stays unaware of it. The input handler below is what
    // actually strips the command text and flips `active` on.
    box.dispatchEvent(new Event("input"));
    box.focus();
  };

  box.addEventListener("input", () => {
    if (active) {
      // The command's own text is gone; whatever is left is the fact being
      // typed, not something to re-parse for "/memory" every keystroke.
      panel.hidden = false;
      return;
    }

    const remainder = isMemoryCommand(box.value);
    if (remainder !== null) {
      // Just became complete -- typed out in full, or accepted below. Its
      // job is done: replace it with the fact alone and hand off to the
      // chip, which is now the only visible sign of the mode.
      active = true;
      box.value = remainder;
      panel.hidden = false;
      return;
    }

    panel.hidden = !shouldSuggest(box.value);
  });

  box.addEventListener("keydown", (event) => {
    // Backspace (or Delete) against an already-empty box is the way out of
    // the mode once entered -- there is nothing left to delete otherwise,
    // so the keystroke is free to mean "never mind" instead.
    if (active && box.value === "" && (event.key === "Backspace" || event.key === "Delete")) {
      resetMemoryMode();
      return;
    }

    // Tab or Enter completes the command while it is still an incomplete
    // prefix, same as a filename completion. Enter needs
    // stopImmediatePropagation, not just preventDefault: app.js's own
    // Enter-to-send listener is bound on this same `box`, registered after
    // this one, and would otherwise still fire for the same keydown and
    // send the unfinished text (e.g. "/mem") as a literal question. Once
    // the command is complete this no longer applies -- Enter has to reach
    // that listener instead, which is why this is gated on shouldSuggest
    // directly rather than on whether the chip happens to be visible.
    if (shouldSuggest(box.value) && (event.key === "Tab" || event.key === "Enter")) {
      event.preventDefault();
      event.stopImmediatePropagation();
      accept();
    }
  });

  // A click on the chip fires after this unless it is deferred — the blur
  // would otherwise hide the panel first and swallow the click. Only
  // matters pre-activation: active mode's chip is not a button meant to be
  // clicked away, just an indicator, and accept() is already a no-op then.
  box.addEventListener("blur", () => setTimeout(() => { panel.hidden = !active; }, 150));
  // Refocusing the box after a blur restores the chip if the mode is still
  // active -- blur hides it, but leaving the mode is Backspace's job above,
  // not a side effect of clicking away and back.
  box.addEventListener("focus", () => { if (active) panel.hidden = false; });

  item.addEventListener("click", accept);
}

// --- picking up the worker's reply -------------------------------------------

const POLL_MS = 2000;
// ~90s of polling — generous next to how long a single extraction call takes,
// and bounded so a dead worker does not poll this tab forever. If it never
// arrives, the reply still shows up next time the chat is opened.
const MAX_ATTEMPTS = 45;

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

// One watcher per chat at a time — reopening the same notebook while a
// `/memory` reply is still pending must not start a second poller racing the
// first to render the same message twice.
const watching = new Set();

async function pollUntilNewMessage(chatId, knownCount) {
  for (let attempt = 0; attempt < MAX_ATTEMPTS; attempt++) {
    await sleep(POLL_MS);

    // The reader switched notebooks; this chat is no longer on screen.
    if (state.notebook?.chat_id !== chatId) return;

    let turns;
    try {
      ({ messages: turns } = await api.listMessages(chatId));
    } catch {
      continue; // a transient read failure, not a reason to give up
    }

    if (turns.length > knownCount) {
      // plain: this is the worker's "Saved: ..." reply, a confirmation, not
      // an answer — see renderMessage's own doc for why that means no
      // Save/Copy/Download row.
      turns.slice(knownCount).forEach((message) => renderMessage(message, { plain: true }));
      return;
    }
  }
}

/** Start watching *chatId* for the assistant message extract_memory_task
 * writes once it finishes. Establishes its own baseline message count rather
 * than trusting the caller's, so a miscount in the transcript's own DOM can
 * never desync it from what the server actually holds.
 */
export async function watchForMemoryReply(chatId) {
  if (watching.has(chatId)) return;
  watching.add(chatId);

  try {
    let knownCount;

    try {
      const { messages: turns } = await api.listMessages(chatId);
      knownCount = turns.length;
    } catch {
      return; // no baseline to compare against; give up quietly
    }

    await pollUntilNewMessage(chatId, knownCount);
  } finally {
    watching.delete(chatId);
  }
}

// --- the memory-search button ------------------------------------------------

/** Wire the memory-search button: click reads the current draft, searches
 * stored facts against it, and shows up to `MemoryController.MEMORY_TOP_K`
 * ranked results in a floating panel — a preview of what's relevant to what
 * someone is about to ask. Distinct from the automatic, unranked fold every
 * answer already gets server-side (ChatController.answer_stream) — nothing
 * to wire here for that half.
 */
export function bindMemoryButton() {
  const box = $("question");
  const btn = $("btn-memory-search");
  const panel = $("memory-results");
  const list = $("memory-results-list");

  if (!box || !btn || !panel || !list) return;

  btn.title = t("memoryButtonTitle");

  const hide = () => { panel.hidden = true; };

  const renderResults = (memories) => {
    list.replaceChildren();

    if (!memories.length) {
      const empty = document.createElement("li");
      empty.className = "sources-block__item";
      empty.textContent = t("memoryNoResults");
      list.append(empty);
      return;
    }

    memories.forEach((mem) => list.append(memoryFactItem(mem)));
  };

  btn.addEventListener("click", async () => {
    const draft = box.value.trim();

    if (!draft) {
      toast(t("memoryTypeFirst"));
      return;
    }

    if (!state.notebook) {
      toast(t("noNotebookYet"));
      return;
    }

    let memories;
    try {
      ({ memories } = await api.searchMemory(state.notebook.chat_id, draft));
    } catch {
      return; // a failed search is not worth interrupting the composer over
    }

    renderResults(memories);
    panel.hidden = false;
  });

  // The draft the results were about is gone the moment it changes further —
  // rather than let a stale panel sit there, hide it and let another click
  // ask again.
  box.addEventListener("input", hide);
}
