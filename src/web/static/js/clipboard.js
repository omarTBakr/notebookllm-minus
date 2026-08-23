// Highlighting text copies it.
//
// Selecting is already the gesture people make before Ctrl+C, so the second
// half is dropped: whatever you highlight is on the clipboard by the time you
// let go. Useful here because the point of the app is moving text out of an
// answer and into a note.
//
// It hangs off mouseup/keyup rather than selectionchange because writeText
// wants the user activation those events carry, and because selectionchange
// fires on every pixel of a drag.

import { t } from "./i18n.js";
import { toast } from "./soon.js";

// A click that drags a pixel selects a character. That is not an intent.
const MIN_LENGTH = 2;

// What is already on the clipboard, so releasing the mouse twice over one
// selection does not flash twice.
let copied = "";

// The one confirmation on screen. A triple-click really is two copies — the
// word, then the line — but it should read as one, so the second replaces
// the first instead of stacking beside it.
let note = null;

async function copySelection() {
  const text = (window.getSelection()?.toString() ?? "").trim();

  // Note that selecting inside an <input> or <textarea> reads as empty here,
  // which is what we want: selecting to type over something should not
  // quietly replace your clipboard.
  if (text.length < MIN_LENGTH || text === copied) return;

  try {
    await navigator.clipboard.writeText(text);
    copied = text;
    note?.remove();
    // Short — this fires often, and it is a confirmation, not a message.
    note = toast(t("copied"), 1100);
  } catch {
    // Denied permission, or an insecure origin. Highlighting text must not
    // produce an error nobody asked for and nobody can act on.
  }
}

export function bindAutoCopy() {
  // A new drag is a new intent, even onto the same words.
  document.addEventListener("mousedown", () => { copied = ""; });
  document.addEventListener("mouseup", copySelection);

  // Keyboard selection: Shift+arrows, and Ctrl/Cmd+A.
  document.addEventListener("keyup", (event) => {
    if (event.shiftKey || event.key === "a" || event.key === "A") copySelection();
  });
}
