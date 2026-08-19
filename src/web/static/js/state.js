// The little shared state the view modules read, plus what persists across
// reloads. localStorage holds only ids — there is nothing secret to keep.

const USER_KEY = "notebookllm.user_id";

export const state = {
  userId: null,
  sessions: [],
  chats: new Map(),   // sessionId -> chats[]
  activeChat: null,   // the full chat object, including `grounded`
  streaming: false,
};

export const storedUserId = () => localStorage.getItem(USER_KEY);
export const rememberUser = (id) => localStorage.setItem(USER_KEY, id);
export const forgetUser = () => localStorage.removeItem(USER_KEY);

export const storedLang = () => localStorage.getItem("lang");
