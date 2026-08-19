// Bootstrap: wire the DOM to the view modules and hold the composer logic.

import { api } from "./api.js";
import { applyLang, currentLang, t } from "./i18n.js";
import { ask, renderHistory, showEmptyState } from "./chat.js";
import {
  bindChatSelection,
  createChat,
  createSession,
  createUser,
  initUser,
  loadSessions,
  renderSessions,
} from "./sidebar.js";
import { bindModelPickers, loadCatalogue, showFor } from "./models.js";
import { state, storedLang } from "./state.js";

const $ = (id) => document.getElementById(id);

function toast(message) {
  const box = document.createElement("div");
  box.className = "toast";
  box.textContent = message;
  document.body.append(box);
  setTimeout(() => box.remove(), 5000);
}

// --- chat selection ----------------------------------------------------------

async function selectChat(chatId) {
  const chat = await api.getChat(chatId);
  state.activeChat = chat;

  $("chat-title").textContent = chat.title;
  $("chat-meta").textContent = `${chat.lang.toUpperCase()} · ${chat.chat_id.slice(0, 8)}`;
  $("grounded-badge").classList.toggle("badge--hidden", !chat.grounded);

  $("question").disabled = false;
  $("btn-send").disabled = false;

  showFor(chat);
  renderSessions();
  await renderHistory(chatId);
}

/** Re-read the chat so `grounded` reflects the index, not our assumption. */
async function refreshActiveChat() {
  if (!state.activeChat) return;
  const chat = await api.getChat(state.activeChat.chat_id);
  state.activeChat = chat;
  $("grounded-badge").classList.toggle("badge--hidden", !chat.grounded);
}

// --- composer ----------------------------------------------------------------

function autoGrow(textarea) {
  textarea.style.height = "auto";
  textarea.style.height = `${Math.min(textarea.scrollHeight, 180)}px`;
}

async function send() {
  const box = $("question");
  const text = box.value.trim();

  if (!text || state.streaming) return;

  if (!state.activeChat) {
    toast(t("pickChatFirst"));
    return;
  }

  box.value = "";
  autoGrow(box);
  $("btn-send").disabled = true;

  await ask(state.activeChat.chat_id, text, {
    onDone: async () => {
      $("btn-send").disabled = false;
      // The first question renames the chat server-side, so refresh the list.
      await loadSessions();
      renderSessions();
    },
  });
}

async function attach(file) {
  if (!state.activeChat) {
    toast(t("pickChatFirst"));
    return;
  }

  const chip = document.createElement("span");
  chip.className = "file-chip is-pending";
  chip.textContent = `${file.name} — ${t("indexing")}`;
  $("attachments").append(chip);

  try {
    const result = await api.attachDocument(state.activeChat.chat_id, file);
    chip.classList.remove("is-pending");
    chip.textContent = `📎 ${result.filename} · ${result.chunks_indexed}`;
    await refreshActiveChat();
    await loadSessions();
    renderSessions();
  } catch (error) {
    chip.remove();
    toast(error.message);
  }
}

// --- wiring ------------------------------------------------------------------

function bindEvents() {
  $("composer-form").addEventListener("submit", (event) => {
    event.preventDefault();
    send();
  });

  const box = $("question");
  box.addEventListener("input", () => autoGrow(box));
  box.addEventListener("keydown", (event) => {
    // Enter sends; Shift+Enter is a newline — the convention every chat app uses.
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      send();
    }
  });

  $("file-input").addEventListener("change", (event) => {
    const [file] = event.target.files;
    if (file) attach(file);
    event.target.value = "";
  });

  $("btn-new-session").addEventListener("click", () => createSession().catch((e) => toast(e.message)));

  $("btn-new-user").addEventListener("click", async () => {
    await createUser();
    await loadSessions();
    state.activeChat = null;
    $("chat-title").textContent = t("noChat");
    $("chat-meta").textContent = "";
    $("question").disabled = true;
    $("btn-send").disabled = true;
    showEmptyState();
  });

  $("btn-forget-user").addEventListener("click", async () => {
    localStorage.removeItem("notebookllm.user_id");
    location.reload();
  });

  document.querySelectorAll(".btn--lang").forEach((btn) => {
    btn.addEventListener("click", () => {
      applyLang(btn.dataset.lang);
      // Labels changed; anything rendered from them has to be redrawn.
      renderSessions();
      if (!state.activeChat) {
        $("chat-title").textContent = t("noChat");
        showEmptyState();
      }
    });
  });
}

async function showBackend() {
  try {
    const health = await api.health();
    const embed = health.checks?.embedding ?? {};
    $("backend-info").textContent = `${health.generation_model} · ${embed.model ?? "?"}`;
    if (health.status !== "ok") {
      const failed = Object.entries(health.checks)
        .filter(([, c]) => c.status !== "ok")
        .map(([name]) => name);
      toast(`Backend degraded: ${failed.join(", ")}`);
    }
  } catch {
    $("backend-info").textContent = "backend unreachable";
  }
}

async function main() {
  applyLang(storedLang() ?? document.body.dataset.defaultLang ?? "en");
  bindEvents();
  bindModelPickers();
  bindChatSelection((chatId) => selectChat(chatId).catch((e) => toast(e.message)));

  await loadCatalogue();

  try {
    await initUser();
    await loadSessions();

    // A brand-new user has nothing to click, so give them somewhere to type.
    if (!state.sessions.length) {
      const sessionId = await createSession();
      await createChat(sessionId);
    } else {
      showEmptyState();
    }
  } catch (error) {
    toast(error.message);
  }

  showBackend();
}

main();
