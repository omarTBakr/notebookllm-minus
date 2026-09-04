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

  // Cascades server-side: sessions, notebooks, documents, chunks and vectors
  // all go with the profile.
  deleteUser: (userId) => request(`/chat/users/${userId}`, { method: "DELETE" }),

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

  // Same route, ?download=1: the server switches Content-Disposition to
  // attachment with the source's real name, instead of inline with its id.
  sourceDownloadUrl: (chatId, assetId) =>
    `/chat/chats/${chatId}/assets/${assetId}/content?download=1`,

  // Where a chunk sits in its source, and the rectangles to highlight if any
  // were captured at ingest. Fetched on a citation click, not carried on the
  // citation itself — see routes/chat/_pages.py.
  locateChunk: (chatId, assetId, chunkOrder) =>
    request(`/chat/chats/${chatId}/assets/${assetId}/chunks/${chunkOrder}/locate`),

  renameSource: (chatId, assetId, name) =>
    request(`/chat/chats/${chatId}/assets/${assetId}`, patch({ name })),

  deleteSource: (chatId, assetId) =>
    request(`/chat/chats/${chatId}/assets/${assetId}`, { method: "DELETE" }),

  selectSources: (chatId, excluded) =>
    request(`/chat/chats/${chatId}/sources`, patch({ excluded_assets: excluded })),
  // How far the in-flight upload for this notebook has got. Polled while
  // addSource is still outstanding, so it must stay cheap on the server.
  // taskId pins the answer to this upload's own run. Without it the endpoint
  // reports whatever is currently unfinished for the chat, which is the wrong
  // thing to watch when two uploads overlap.
  indexingProgress: (chatId, taskId = null) =>
    request(
      `/chat/chats/${chatId}/indexing` +
      (taskId ? `?task_id=${encodeURIComponent(taskId)}` : "")
    ),

  addSource: (chatId, file) => {
    const form = new FormData();
    form.append("file", file);
    // No Content-Type header: the browser must set the multipart boundary.
    return request(`/chat/chats/${chatId}/documents`, { method: "POST", body: form });
  },

  // --- conversation ---
  listMessages: (chatId) => request(`/chat/chats/${chatId}/messages`),

  // --- settings ---
  // `sources` narrows discovery to a comma-separated subset, so the picker can
  // ask for the cheap providers first and merge the slow ones in as they land.
  listModels: (probeEmbeddings = true, sources = null) =>
    request(
      `/chat/models?probe_embeddings=${probeEmbeddings}` +
      (sources ? `&sources=${encodeURIComponent(sources)}` : "")
    ),
  quickModels: () => request("/chat/models/quick"),
  setModels: (chatId, models) => request(`/chat/chats/${chatId}/models`, patch(models)),
  setSettings: (chatId, settings) => request(`/chat/chats/${chatId}/settings`, patch(settings)),

  // Streams the answer. fetch rather than EventSource because the question
  // belongs in a POST body and EventSource can only issue GETs.
  async *streamMessage(chatId, text, { signal } = {}) {
    // signal is what actually closes the connection when the reader presses
    // Stop. Breaking out of the for-await would end this generator but leave
    // the reader — and the socket — open, so the model would keep generating
    // into a response nobody is reading.
    const response = await fetch(`/chat/chats/${chatId}/message`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
      signal,
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
