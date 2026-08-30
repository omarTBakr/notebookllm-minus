// Middle panel: the transcript, and streaming an answer into it.

import { api } from "./api.js";
import { t } from "./i18n.js";
import { renderInto } from "./markdown.js";
import { state } from "./state.js";
import { $ } from "./dom.js";

const messages = () => $("messages");

// Clicking a citation opens the source in the sources panel. Registered from
// app.js rather than imported, matching bindSourcesChanged — the two panels
// stay unaware of each other and there is no import edge between them.
let onCitationClick = null;

export function bindCitationClick(handler) {
  onCitationClick = handler;
}

// Copying and saving are owned by other modules — the clipboard primitive by
// clipboard.js, the upload by sources.js. Registered from app.js so chat.js
// gains no import edge to either.
let onCopyAnswer = null;
let onSaveAnswer = null;

export function bindAnswerActions({ copy, save }) {
  onCopyAnswer = copy;
  onSaveAnswer = save;
}

export function clear() {
  messages().replaceChildren();
}

// How far from the bottom still counts as "following along". Roughly one line
// of text plus the gap, so a stray trackpad nudge does not read as intent.
const FOLLOW_SLACK = 120;

function nearBottom(el) {
  return el.scrollHeight - el.scrollTop - el.clientHeight < FOLLOW_SLACK;
}

function scrollToEnd() {
  const el = $("transcript");
  el.scrollTop = el.scrollHeight;
}

/** Keep the newest text in view — but only while the reader is at the bottom.
 *
 * Called after each render rather than after each delta. frameRenderer only
 * schedules a frame; the DOM is not mutated until that frame runs, so
 * scrolling straight after update() measured a scrollHeight that did not yet
 * include the new text and left the transcript one paint behind forever. It
 * unstuck itself when generation stopped only because the final flush renders
 * synchronously before its scroll.
 */
function followEnd() {
  const el = $("transcript");
  if (following) el.scrollTop = el.scrollHeight;
}

// Scrolling up during an answer means "I am reading" — the transcript stops
// chasing the bottom until the reader returns there.
let following = true;

// The in-flight answer, so Stop has something to abort.
let inFlight = null;

/** Stop the answer being generated, keeping whatever has arrived so far. */
export function stopAnswer() {
  inFlight?.abort();
}

export function bindTranscript() {
  const el = $("transcript");
  el.addEventListener("scroll", () => { following = nearBottom(el); }, { passive: true });
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

    // The label used to end in `· #<chunk_order>`, which reads like a page
    // number and is not one — it counts chunks within the document. A chunk
    // is an artefact of how the text was cut up and means nothing to a reader.
    const label = cite.page_label
      ? `${cite.source} · ${t("page")} ${cite.page_label}`
      : cite.source;

    // Clickable whenever the chunk it names still exists — page_number is a
    // PDF-only concept (a .txt/.md chunk has none) but /locate finds its own
    // highlight from chunk_order alone. Neither exists for a citation stored
    // before locating was added, or whose chunk has since been deleted.
    const canOpen =
      cite.asset_id !== null &&
      cite.asset_id !== undefined &&
      cite.chunk_order !== null &&
      cite.chunk_order !== undefined;

    let source;

    if (canOpen) {
      // A button, not an anchor: this opens a panel in place, and an <a href>
      // would offer "open in new tab" on a URL that is not a page.
      source = document.createElement("button");
      source.type = "button";
      source.className = "sources-block__link";
      source.title = cite.page_number ? t("openAtPage") : t("openSource");
      source.addEventListener("click", () => {
        onCitationClick?.(cite.asset_id, cite.page_number, cite.chunk_order);
      });
    } else {
      source = document.createElement("span");
    }

    source.textContent = label;

    const score = document.createElement("span");
    score.className = "sources-block__score";
    score.textContent = cite.score ?? "";

    item.append(num, source, score);
    list.append(item);
  }

  details.append(summary, list);
  return details;
}

/** One action button. `soon` marks the ones with nothing behind them yet.
 *
 * The i18n key is kept on the element as well as resolved, because applyLang
 * only re-reads [data-i18n] and these are built in JS — without it the labels
 * would keep the old language after a switch.
 */
function actionButton(key, onClick, { soon } = {}) {
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "btn btn--outline btn--pill";
  btn.dataset.i18n = key;
  btn.textContent = t(key);

  if (soon) {
    // Handled by the capture-phase listener in soon.js, which swallows the
    // click — so a button carrying this can never also have a real handler.
    btn.dataset.soon = soon;
  } else {
    btn.addEventListener("click", () => onClick(btn));
  }

  return btn;
}

/** The row of actions under a finished answer.
 *
 * Takes the answer's *markdown*, not its rendered DOM: copying or saving
 * `body.textContent` would hand over text with the list markers, code fences
 * and link targets already stripped out by the renderer.
 */
function answerActions(markdown) {
  const row = document.createElement("div");
  row.className = "msg__actions";

  row.append(
    actionButton("saveToSources", async (btn) => {
      btn.disabled = true;
      try {
        await onSaveAnswer?.(markdown);
      } finally {
        btn.disabled = false;
      }
    }),
    actionButton("copyAnswer", () => onCopyAnswer?.(markdown)),
    actionButton("download", () => downloadMarkdown(markdown)),
    // No backend for these, so they stay marked.
    actionButton("helpful", null, { soon: "Feedback" }),
    actionButton("notHelpful", null, { soon: "Feedback" }),
  );

  return row;
}

/** Save the answer's markdown to a file.
 *
 * The first download in the app. Markdown rather than the rendered text so
 * tables, headings and fences survive; text/markdown rather than text/html
 * because nothing here should ever hand the browser a document to execute.
 */
function downloadMarkdown(markdown) {
  const stamp = new Date().toISOString().slice(0, 10);
  const blob = new Blob([markdown], { type: "text/markdown;charset=utf-8" });
  const url = URL.createObjectURL(blob);

  const link = document.createElement("a");
  link.href = url;
  link.download = `answer-${stamp}.md`;
  link.click();

  // Freed on the next turn of the loop: revoking synchronously can cancel the
  // download in some browsers before it has read the blob.
  setTimeout(() => URL.revokeObjectURL(url), 0);
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
    wrap.append(answerActions(message.content));
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

function stoppedNote() {
  const note = document.createElement("p");
  note.className = "muted";
  note.style.fontSize = "0.8rem";
  note.dataset.i18n = "stopped";
  note.textContent = t("stopped");
  return note;
}

/** Send a question and stream the answer into a new bubble. */
export async function ask(chatId, text, { onDone } = {}) {
  const question = bubble("user");
  question.body.textContent = text;

  const answer = bubble("assistant");
  answer.body.classList.add("md", "is-streaming");
  answer.body.textContent = "";

  // A new question is always worth following, whatever the reader was
  // doing when the previous answer finished.
  following = true;
  state.streaming = true;
  inFlight = new AbortController();

  let grounded = false;
  let citations = [];
  let received = 0;
  let markdown = "";
  let thinking = null;
  let reasoning = "";

  // onRender, not a call after update(): see followEnd.
  const answerRender = frameRenderer(answer.body, { onRender: followEnd });

  try {
    for await (const event of api.streamMessage(chatId, text, {
      signal: inFlight.signal,
    })) {
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
        followEnd();
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
      } else if (event.type === "error") {
        answer.body.classList.add("msg__error");
        const note = document.createElement("p");
        note.textContent = event.detail;
        answer.body.append(note);
      }
    }

    if (!grounded && received) noteUngrounded(answer.wrap);
    if (received) answer.wrap.append(answerActions(markdown));
  } catch (error) {
    // Stop is a deliberate ending, not a failure: keep the partial answer and
    // give it the same action row a finished one gets. Painting it red, or
    // replacing it with "AbortError", would throw away text the reader asked
    // to keep.
    if (error.name === "AbortError") {
      if (received) {
        answer.wrap.append(stoppedNote());
        answer.wrap.append(answerActions(markdown));
      }
    } else {
      answer.body.classList.add("msg__error");
      answer.body.textContent = error.message;
    }
  } finally {
    inFlight = null;
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
