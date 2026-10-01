// Right panel: the note editor.
//
// A note is not a new kind of thing. It is written to a .txt File in the
// browser and pushed through the same upload the file picker uses, so it
// chunks, embeds and answers questions exactly like a document someone
// dropped in — the backend never learns it was typed.

import { t } from "./i18n.js";
import { toast } from "./soon.js";
import { add as addSourceFile, load as reloadSources } from "./sources.js";
import { state } from "./state.js";
import { $ } from "./dom.js";

// The button is a toggle, so its meaning has to be tracked somewhere.
let open = false;
let saving = false;

const NOTE_NAME = /^note(\d+)\.txt$/i;

/** note1, note2, … — the highest number in this notebook, plus one.
 *
 * Highest rather than a count, so renaming or deleting note2 does not hand
 * its number to the next note while note3 is still sitting there.
 */
function nextNoteName() {
  const highest = state.sources.reduce((max, source) => {
    const match = NOTE_NAME.exec(source.name ?? "");
    return match ? Math.max(max, Number(match[1])) : max;
  }, 0);

  return `note${highest + 1}.txt`;
}

function paintButton() {
  const label = $("btn-note-label");
  // Set the key, not just the text: applyLang re-reads data-i18n on every
  // language switch and would otherwise put "Add note" back mid-edit.
  label.dataset.i18n = open ? "saveNote" : "addNote";
  label.textContent = t(label.dataset.i18n);
}

function paintCount() {
  $("note-count").textContent = `${$("note-body").value.length} ${t("chars")}`;
}

/** Labels changed under us — anything built from t() has to be redrawn. */
export function repaint() {
  paintButton();
  paintCount();
}

function show(next) {
  open = next;
  $("note-editor").hidden = !open;
  // Both want the panel's spare room, and the empty state is the less
  // useful of the two while someone is writing.
  $("studio-empty").hidden = open;
  paintButton();
  if (open) $("note-body").focus();
}

function reset() {
  $("note-title").value = "";
  $("note-body").value = "";
  paintCount();
}

async function save() {
  const title = $("note-title").value.trim();
  const body = $("note-body").value.trim();

  if (!body) {
    toast(t("noteEmpty"));
    $("note-body").focus();
    return;
  }

  if (!state.notebook) {
    toast(t("noNotebookYet"));
    return;
  }

  if (saving) return;
  saving = true;
  $("btn-note").disabled = true;

  // The number comes from the source list, so read it again first. Another
  // tab may have added note3 since this one last looked, and naming from a
  // stale snapshot is how two note1.txt end up side by side. This narrows
  // the window to the length of one request; it does not close it — only the
  // server can do that, by assigning the name itself.
  await reloadSources(state.notebook.chat_id);

  // The title belongs in the text, not only in the name: retrieval sees chunk
  // contents and nothing else, so a title kept only in the filename is lost.
  const text = title ? `${title}\n\n${body}` : body;
  const file = new File([text], nextNoteName(), { type: "text/plain" });

  const saved = await addSourceFile(file);

  saving = false;
  $("btn-note").disabled = false;

  // On failure add() has already said why. Keep the editor open and the
  // text in it — a rejected upload must not eat what someone just wrote.
  if (!saved) return;

  reset();
  show(false);
  toast(t("noteSaved"));
}

export function bindStudio() {
  $("btn-note").addEventListener("click", () => (open ? save() : show(true)));

  $("btn-note-discard").addEventListener("click", () => {
    reset();
    show(false);
  });

  $("note-body").addEventListener("input", paintCount);

  $("note-title").addEventListener("keydown", (event) => {
    // Enter in a one-line title means "on to the body", not "submit".
    if (event.key === "Enter") {
      event.preventDefault();
      $("note-body").focus();
    }
  });

  $("note-body").addEventListener("keydown", (event) => {
    // Ctrl/Cmd+Enter saves. Plain Enter cannot: a note is mostly newlines.
    if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
      event.preventDefault();
      save();
    }
  });

  // Escape closes the editor without discarding — reopening brings the text
  // back, so a mistaken keypress costs nothing.
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && open) show(false);
  });

  repaint();
}
