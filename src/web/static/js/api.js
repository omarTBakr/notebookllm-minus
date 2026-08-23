// Every call to the backend lives here, so no view module builds a URL.

/** The message a failed response carries.
 *
 * The API reports failures as {"detail": "..."} from a single handler, so one
 * unwrap covers every endpoint — including the streaming one, which used to
 * carry its own slightly different copy of this.
 */
async function detailOf(response) {
  try {
    const body = await response.json();
    if (body.detail) return body.detail;
  } catch { /* not JSON — fall through to the status line */ }

  return `${response.status} ${response.statusText}`;
}

async function request(path, options = {}) {
  const response = await fetch(path, options);

  if (!response.ok) {
    const error = new Error(await detailOf(response));
    error.status = response.status;
    throw error;
  }

  return response.json();
}

const post = (body) => ({
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

const patch = (body) => ({
  method: "PATCH",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

export const api = {
  health: () => request("/nlp/health"),

  // --- profiles ---
  createUser: (label = "") => request("/chat/users", post({ label })),
  listUsers: () => request("/chat/users"),
  getUser: (userId) => request(`/chat/users/${userId}`),
  renameUser: (userId, label) => request(`/chat/users/${userId}`, patch({ label })),

  // --- notebooks (a notebook is a chat; sessions never surface) ---
  listNotebooks: (userId) => request(`/chat/users/${userId}/chats`),
  createNotebook: (userId, title, lang) =>
    request(`/chat/users/${userId}/chats`, post({ title, lang })),
  getNotebook: (chatId) => request(`/chat/chats/${chatId}`),
  renameNotebook: (chatId, title) => request(`/chat/chats/${chatId}`, patch({ title })),

  // --- sources ---
  listSources: (chatId) => request(`/chat/chats/${chatId}/assets`),

  // A URL rather than a request: the bytes go straight into <embed> for a
  // PDF, and only the text branch actually fetches.
  sourceContentUrl: (chatId, assetId) =>
    `/chat/chats/${chatId}/assets/${assetId}/content`,

  renameSource: (chatId, assetId, name) =>
    request(`/chat/chats/${chatId}/assets/${assetId}`, patch({ name })),

  selectSources: (chatId, excluded) =>
    request(`/chat/chats/${chatId}/sources`, patch({ excluded_assets: excluded })),
  addSource: (chatId, file) => {
    const form = new FormData();
    form.append("file", file);
    // No Content-Type header: the browser must set the multipart boundary.
    return request(`/chat/chats/${chatId}/documents`, { method: "POST", body: form });
  },

  // --- conversation ---
  listMessages: (chatId) => request(`/chat/chats/${chatId}/messages`),

  // --- settings ---
  listModels: () => request("/chat/models"),
  setModels: (chatId, models) => request(`/chat/chats/${chatId}/models`, patch(models)),
  setSettings: (chatId, settings) => request(`/chat/chats/${chatId}/settings`, patch(settings)),

  // Streams the answer. fetch rather than EventSource because the question
  // belongs in a POST body and EventSource can only issue GETs.
  async *streamMessage(chatId, text) {
    const response = await fetch(`/chat/chats/${chatId}/message`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    });

    if (!response.ok) throw new Error(await detailOf(response));

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
