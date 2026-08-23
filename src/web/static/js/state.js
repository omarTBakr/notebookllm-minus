// The small shared state the view modules read, and what survives a reload.
// localStorage holds ids only — there is nothing secret to keep.

const USER_KEY = "notebookllm.user_id";
const BOOK_KEY = "notebookllm.notebook_id";

export const state = {
  userId: null,
  userLabel: null,
  notebooks: [],
  notebook: null,   // the full chat object: title, lang, grounded, settings
  sources: [],
  streaming: false,
};

export const storedUserId = () => localStorage.getItem(USER_KEY);
export const rememberUser = (id) => localStorage.setItem(USER_KEY, id);
export const forgetUser = () => localStorage.removeItem(USER_KEY);

// Remembering the open notebook means a reload lands where you left off
// rather than on whichever one happens to be newest.
export const storedNotebookId = () => localStorage.getItem(BOOK_KEY);
export const rememberNotebook = (id) => localStorage.setItem(BOOK_KEY, id);

export const storedLang = () => localStorage.getItem("lang");
