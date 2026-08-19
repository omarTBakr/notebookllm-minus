// Identity, sessions and chats — everything in the left column.

import { api } from "./api.js";
import { currentLang, t } from "./i18n.js";
import { forgetUser, rememberUser, state, storedUserId } from "./state.js";

let onSelectChat = () => {};

export function bindChatSelection(handler) {
  onSelectChat = handler;
}

function setUserBadge() {
  const badge = document.getElementById("user-badge");
  badge.textContent = state.userId ? state.userId.slice(0, 8) : "—";
}

/** Resolve identity on load: reuse the stored id, or mint a new one. */
export async function initUser() {
  const stored = storedUserId();

  if (stored) {
    try {
      await api.getUser(stored);
      state.userId = stored;
      setUserBadge();
      return;
    } catch (error) {
      // A 404 here is routine — the id outlived the database. Falling through
      // to create a fresh user is the right move, not an error to show.
      if (error.status !== 404) throw error;
      forgetUser();
    }
  }

  await createUser();
}

export async function createUser() {
  const { user_id } = await api.createUser();
  state.userId = user_id;
  rememberUser(user_id);
  setUserBadge();
  state.sessions = [];
  state.chats.clear();
  state.activeChat = null;
}

export async function loadSessions() {
  const { sessions } = await api.listSessions(state.userId);
  state.sessions = sessions;

  await Promise.all(
    sessions.map(async (session) => {
      const { chats } = await api.listChats(session.session_id);
      state.chats.set(session.session_id, chats);
    })
  );

  renderSessions();
}

export function renderSessions() {
  const host = document.getElementById("session-list");
  host.innerHTML = "";

  if (!state.sessions.length) {
    const empty = document.createElement("p");
    empty.className = "muted";
    empty.textContent = t("noSessions");
    host.append(empty);
    return;
  }

  for (const session of state.sessions) {
    const box = document.createElement("div");
    box.className = "session";

    const head = document.createElement("div");
    head.className = "session__head";

    const title = document.createElement("span");
    title.textContent = session.title;

    const add = document.createElement("button");
    add.className = "btn";
    add.textContent = `+ ${t("newChat")}`;
    add.addEventListener("click", () => createChat(session.session_id));

    head.append(title, add);

    const list = document.createElement("div");
    list.className = "session__chats";

    for (const chat of state.chats.get(session.session_id) ?? []) {
      const item = document.createElement("button");
      item.className = "chat-item";
      if (state.activeChat?.chat_id === chat.chat_id) item.classList.add("is-active");

      const label = document.createElement("span");
      label.className = "chat-item__title";
      label.textContent = chat.title;

      const badge = document.createElement("span");
      // The paperclip is the at-a-glance answer to "does this chat have docs?"
      badge.textContent = chat.has_documents ? "📎" : "";

      item.append(label, badge);
      item.addEventListener("click", () => onSelectChat(chat.chat_id));
      list.append(item);
    }

    box.append(head, list);
    host.append(box);
  }
}

export async function createSession() {
  const count = state.sessions.length + 1;
  const { session_id } = await api.createSession(state.userId, `${t("newSession")} ${count}`);
  await loadSessions();
  return session_id;
}

export async function createChat(sessionId) {
  const { chat_id } = await api.createChat(sessionId, t("newChat"), currentLang());
  await loadSessions();
  onSelectChat(chat_id);
  return chat_id;
}
