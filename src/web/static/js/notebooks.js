// Notebooks: the switcher in the top bar, creating one, and renaming it.
//
// A notebook is a chat. Sessions still exist in Mongo but the interface has no
// concept of them — the backend files new notebooks under an implicit one.

import { api } from "./api.js";
import { currentLang, t } from "./i18n.js";
import { toast } from "./soon.js";
import { rememberNotebook, state, storedNotebookId } from "./state.js";
import { $ } from "./dom.js";

let onOpen = () => {};

export function bindNotebookOpen(handler) {
  onOpen = handler;
}

function closeMenu() {
  $("notebook-menu").hidden = true;
}

export function renderList() {
  const host = $("notebook-list");
  host.replaceChildren();

  $("notebook-count").textContent = state.notebooks.length;

  if (!state.notebooks.length) {
    const empty = document.createElement("p");
    empty.className = "field__hint";
    empty.textContent = t("noNotebooks");
    host.append(empty);
    return;
  }

  for (const book of state.notebooks) {
    const item = document.createElement("button");
    item.className = "dropdown__item";
    if (book.chat_id === state.notebook?.chat_id) item.classList.add("is-active");
    item.title = book.title;

    const dot = document.createElement("span");
    dot.className = "dot";
    // The paperclip is the at-a-glance answer to "does this one have sources?"
    dot.textContent = book.has_documents ? "📎" : "📔";

    const name = document.createElement("span");
    name.textContent = book.title;

    item.append(dot, name);
    item.addEventListener("click", () => {
      closeMenu();
      if (book.chat_id !== state.notebook?.chat_id) onOpen(book.chat_id);
    });

    host.append(item);
  }
}

export async function loadList() {
  const { chats } = await api.listNotebooks(state.userId);
  state.notebooks = chats;
  renderList();
  return chats;
}

export async function create() {
  const book = await api.createNotebook(state.userId, t("untitled"), currentLang());
  await loadList();
  rememberNotebook(book.chat_id);
  onOpen(book.chat_id);
  return book;
}

/** The notebook to show on load.
 *
 * ?notebook=<id> wins, so a notebook can be linked to directly; then the one
 * you had open last; then the newest.
 */
export function initialNotebookId() {
  // Not checked against the list: a link should open the notebook it names
  // even when it belongs to another profile, and app.js adopts that profile.
  const requested = new URLSearchParams(location.search).get("notebook");

  if (requested) return requested;

  const stored = storedNotebookId();

  if (stored && state.notebooks.some((b) => b.chat_id === stored)) return stored;

  return state.notebooks[0]?.chat_id ?? null;
}

async function rename() {
  if (!state.notebook) {
    toast(t("noNotebookYet"));
    return;
  }

  const next = prompt(t("renameNotebook"), state.notebook.title);
  if (next === null) return;

  const title = next.trim();
  if (!title) return;

  try {
    await api.renameNotebook(state.notebook.chat_id, title);
    state.notebook.title = title;
    paintTitle();
    await loadList();
  } catch (error) {
    toast(error.message);
  }
}

/** The title appears twice — top bar and the transcript's hero. */
export function paintTitle() {
  const title = state.notebook?.title ?? t("untitled");
  $("notebook-title").textContent = title;
  $("hero-title").textContent = title;
}

export function paintMeta() {
  const count = state.sources.length;
  const word = count === 1 ? t("source") : t("sourcesCount");

  // The composer reports what a question will search, which is not the same
  // as how many sources exist once some are switched off.
  const selected = state.sources.filter((s) => s.selected !== false).length;

  const created = state.notebook
    ? new Date(state.notebook.created_at).toLocaleDateString(undefined, {
        year: "numeric",
        month: "short",
        day: "numeric",
      })
    : "";

  $("hero-meta").textContent = state.notebook ? `${count} ${word} · ${created}` : "";
  $("composer-count").textContent =
    selected === count ? `${count} ${word}` : `${selected}/${count} ${word}`;
}

export function bindNotebooks() {
  $("btn-create-notebook").addEventListener("click", () =>
    create().catch((error) => toast(error.message))
  );

  $("notebook-title").addEventListener("click", rename);

  $("btn-notebooks").addEventListener("click", (event) => {
    event.stopPropagation();
    const menu = $("notebook-menu");
    menu.hidden = !menu.hidden;
    if (!menu.hidden) renderList();
  });

  document.addEventListener("click", (event) => {
    if (!$("notebook-menu").hidden && !event.target.closest("#notebook-menu")) closeMenu();
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") closeMenu();
  });
}
