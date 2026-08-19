// Every call to the backend lives here, so no view module builds a URL.

async function request(path, options = {}) {
  const response = await fetch(path, options);

  if (!response.ok) {
    // The API reports failures as {"detail": "..."} from a single handler,
    // so one unwrap covers every endpoint.
    let detail = `${response.status} ${response.statusText}`;
    try {
      const body = await response.json();
      if (body.detail) detail = body.detail;
    } catch { /* not JSON — keep the status line */ }

    const error = new Error(detail);
    error.status = response.status;
    throw error;
  }

  return response.json();
}

const json = (body) => ({
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

export const api = {
  health: () => request("/nlp/health"),

  createUser: () => request("/chat/users", { method: "POST" }),
  getUser: (userId) => request(`/chat/users/${userId}`),

  createSession: (userId, title) =>
    request(`/chat/users/${userId}/sessions`, json({ title })),
  listSessions: (userId) => request(`/chat/users/${userId}/sessions`),

  createChat: (sessionId, title, lang) =>
    request(`/chat/sessions/${sessionId}/chats`, json({ title, lang })),
  listChats: (sessionId) => request(`/chat/sessions/${sessionId}/chats`),

  getChat: (chatId) => request(`/chat/chats/${chatId}`),

  listModels: () => request("/chat/models"),

  setModels: (chatId, models) =>
    request(`/chat/chats/${chatId}/models`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(models),
    }),
  listMessages: (chatId) => request(`/chat/chats/${chatId}/messages`),

  attachDocument: (chatId, file) => {
    const form = new FormData();
    form.append("file", file);
    // No Content-Type header: the browser must set the multipart boundary.
    return request(`/chat/chats/${chatId}/documents`, { method: "POST", body: form });
  },

  // Streams the answer. Uses fetch rather than EventSource because the
  // question belongs in a POST body and EventSource can only issue GETs.
  async *streamMessage(chatId, text, signal) {
    const response = await fetch(`/chat/chats/${chatId}/message`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
      signal,
    });

    if (!response.ok) {
      let detail = `${response.status} ${response.statusText}`;
      try { detail = (await response.json()).detail ?? detail; } catch {}
      throw new Error(detail);
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });

      // SSE frames are separated by a blank line. Keep the trailing partial
      // frame in the buffer until the rest of it arrives.
      const frames = buffer.split("\n\n");
      buffer = frames.pop();

      for (const frame of frames) {
        const line = frame.split("\n").find((l) => l.startsWith("data: "));
        if (line) yield JSON.parse(line.slice(6));
      }
    }
  },
};
