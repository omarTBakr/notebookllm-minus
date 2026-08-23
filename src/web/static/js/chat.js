// Middle panel: the transcript, and streaming an answer into it.

import { api } from "./api.js";
import { t } from "./i18n.js";
import { renderInto } from "./markdown.js";
import { state } from "./state.js";
import { $ } from "./dom.js";

const messages = () => $("messages");

export function clear() {
  messages().replaceChildren();
}

function scrollToEnd() {
  const el = $("transcript");
  el.scrollTop = el.scrollHeight;
}

/** Render Markdown into *el*, at most once per animation frame.
 *
 * A reasoning answer arrives as hundreds of small deltas, and each one
 * re-parses the whole text so far. Coalescing onto frames keeps that off the
 * critical path — the browser paints once per frame regardless.
 */
function frameRenderer(el, { onRender } = {}) {
  let pending = "";
  let frame = 0;

  const paint = () => {
    frame = 0;
    renderInto(el, pending);
    onRender?.();
  };

  return {
    update(text) {
      pending = text;
      if (!frame) frame = requestAnimationFrame(paint);
    },
    // The last delta may land between frames; flush so nothing is dropped.
    flush() {
      if (frame) cancelAnimationFrame(frame);
      paint();
    },
  };
}

// textContent everywhere, never innerHTML, so a document containing markup is
// shown as text rather than injected into the page.
function bubble(role) {
  const wrap = document.createElement("article");
  wrap.className = `msg msg--${role}`;

  const label = document.createElement("span");
  label.className = "msg__role";
  label.textContent = role === "user" ? t("you") : t("assistant");

  const body = document.createElement("div");
  body.className = "msg__body";

  wrap.append(label, body);
  messages().append(wrap);
  scrollToEnd();

  return { wrap, body };
}

function citationsBlock(citations) {
  const details = document.createElement("details");
  details.className = "sources-block";

  const summary = document.createElement("summary");
  summary.textContent = `${t("citations")} (${citations.length})`;

  const list = document.createElement("ul");
  list.className = "sources-block__list";

  for (const cite of citations) {
    const item = document.createElement("li");
    item.className = "sources-block__item";

    const num = document.createElement("span");
    num.className = "sources-block__num";
    num.textContent = `[${cite.num}]`;

    const source = document.createElement("span");
    source.textContent =
      cite.chunk_order === null || cite.chunk_order === undefined
        ? cite.source
        : `${cite.source} · #${cite.chunk_order}`;

    const score = document.createElement("span");
    score.className = "sources-block__score";
    score.textContent = cite.score ?? "";

    item.append(num, source, score);
    list.append(item);
  }

  details.append(summary, list);
  return details;
}

/** The row of actions under a finished answer. All of it is planned work. */
function answerActions() {
  const row = document.createElement("div");
  row.className = "msg__actions";

  for (const [label, feature] of [
    ["📌 " + "Save to note", "Save to note"],
    ["⧉", "Copy answer"],
    ["👍", "Feedback"],
    ["👎", "Feedback"],
  ]) {
    const btn = document.createElement("button");
    btn.className = "btn btn--outline btn--pill";
    btn.textContent = label;
    btn.dataset.soon = feature;
    row.append(btn);
  }

  return row;
}

/** Live scratchpad. Open while the model reasons, collapsed once it answers. */
function thinkingPanel() {
  const details = document.createElement("details");
  details.className = "thinking";
  details.open = true;

  const summary = document.createElement("summary");
  summary.textContent = t("thinking");

  const body = document.createElement("div");
  body.className = "thinking__body md";

  details.append(summary, body);

  // The panel scrolls internally; keep the newest reasoning in view.
  const render = frameRenderer(body, {
    onRender: () => { body.scrollTop = body.scrollHeight; },
  });

  return { details, summary, body, render };
}

export function renderMessage(message) {
  const { body, wrap } = bubble(message.role === "user" ? "user" : "assistant");

  if (message.role === "user") {
    // The user's own words, shown exactly as typed.
    body.textContent = message.content;
  } else {
    body.classList.add("md");
    renderInto(body, message.content);
    if (message.citations?.length) wrap.append(citationsBlock(message.citations));
    wrap.append(answerActions());
  }
}

export async function loadHistory(chatId) {
  clear();

  if (!chatId) return;

  const { messages: turns } = await api.listMessages(chatId);
  turns.forEach(renderMessage);
  scrollToEnd();
}

function noteUngrounded(wrap) {
  const note = document.createElement("p");
  note.className = "muted";
  note.style.fontSize = "0.8rem";
  note.textContent = t("ungrounded");
  wrap.append(note);
}

/** Send a question and stream the answer into a new bubble. */
export async function ask(chatId, text, { onDone } = {}) {
  const question = bubble("user");
  question.body.textContent = text;

  const answer = bubble("assistant");
  answer.body.classList.add("md", "is-streaming");
  answer.body.textContent = "";

  state.streaming = true;

  let grounded = false;
  let citations = [];
  let received = 0;
  let markdown = "";
  let thinking = null;
  let reasoning = "";

  const answerRender = frameRenderer(answer.body);

  try {
    for await (const event of api.streamMessage(chatId, text)) {
      if (event.type === "meta") {
        grounded = event.grounded;
        citations = event.citations ?? [];
        // Sources are appended before the first token so the reader can see
        // what the answer is about to be based on.
        if (citations.length) answer.wrap.append(citationsBlock(citations));
      } else if (event.type === "thinking") {
        // Created lazily: models that don't reason never show an empty panel.
        if (!thinking) {
          thinking = thinkingPanel();
          answer.wrap.insertBefore(thinking.details, answer.body);
        }
        reasoning += event.text;
        thinking.render.update(reasoning);
        scrollToEnd();
      } else if (event.type === "delta") {
        received += 1;
        // The answer has begun, so fold the scratchpad away.
        if (thinking && thinking.details.open) {
          thinking.render.flush();
          thinking.details.open = false;
          thinking.summary.textContent =
            `${t("thoughtFor")} ${reasoning.length} ${t("chars")}`;
        }
        markdown += event.text;
        // Re-render the whole answer rather than appending: a bold run or a
        // list only becomes recognisable once its closing syntax arrives.
        answerRender.update(markdown);
        scrollToEnd();
      } else if (event.type === "error") {
        answer.body.classList.add("msg__error");
        const note = document.createElement("p");
        note.textContent = event.detail;
        answer.body.append(note);
      }
    }

    if (!grounded && received) noteUngrounded(answer.wrap);
    if (received) answer.wrap.append(answerActions());
  } catch (error) {
    answer.body.classList.add("msg__error");
    answer.body.textContent = error.message;
  } finally {
    if (markdown) answerRender.flush();
    if (thinking) {
      thinking.render.flush();
      thinking.details.open = false;
    }
    answer.body.classList.remove("is-streaming");
    state.streaming = false;
    scrollToEnd();
    onDone?.({ grounded });
  }
}
