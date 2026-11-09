// The small shared state the view modules read, and what survives a reload.
// localStorage holds UI preferences and the open notebook's id only. Who the
// user is comes from the session cookie, which scripts cannot read.

const BOOK_KEY = "notebookllm.notebook_id";

export const state = {
  userId: null,
  userLabel: null,
  userEmail: null,
  notebooks: [],
  notebook: null,   // the full chat object: title, lang, grounded, settings
  sources: [],
  streaming: false,
};

// Remembering the open notebook means a reload lands where you left off
// rather than on whichever one happens to be newest.
export const storedNotebookId = () => localStorage.getItem(BOOK_KEY);
export const rememberNotebook = (id) => localStorage.setItem(BOOK_KEY, id);

export const storedLang = () => localStorage.getItem("lang");
