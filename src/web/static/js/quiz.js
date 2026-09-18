// Right panel: the quiz.
//
// One question at a time, four options, answer revealed on choosing, with a
// running score and a link to the page the question came from. Like the deck,
// it fills while being taken.
//
// Answers are kept by question index rather than mutated onto the item,
// because the item list is replaced wholesale on every poll — writing state
// onto an object the next poll discards would silently lose the score.

import { $ } from "./dom.js";
import { citable, cite, generate, resume, takeOverPanel } from "./artifacts.js";
import { clampIndex, score as scoreAnswers } from "./artifact_items.js";
import { t } from "./i18n.js";
import { toast } from "./soon.js";

const KIND = "quiz";

let questions = [];
let index = 0;
let status = "";

// index -> the option the user picked. Absent means unanswered.
let answers = new Map();

export function bindQuiz() {
  const tile = document.querySelector(".studio-card--quiz");

  tile?.addEventListener("click", async () => {
    open();

    // Show what is already there before making anything. Clicking the tile
    // used to start a generation unconditionally, and starting one *clears
    // the set* — so opening a finished deck wiped it and put the reader back
    // on "reading the documents…" to wait for a replacement they never asked
    // for. Regenerating on purpose is what the "Generate again" button is.
    const existing = await resume(KIND, { onItems: setQuestions, onStatus: setStatus });

    if (!existing) {
      generate(KIND, { onItems: setQuestions, onStatus: setStatus });
    }
  });

  $("quiz-close")?.addEventListener("click", close);
  $("quiz-prev")?.addEventListener("click", () => step(-1));
  $("quiz-next")?.addEventListener("click", () => step(1));
  $("quiz-cite")?.addEventListener("click", () =>
    // Awaited internally; a failure to open is reported rather than left as
    // an unhandled rejection with a button that silently did nothing.
    cite(questions[index]).catch((e) => toast(e.message)),
  );

  $("quiz-again")?.addEventListener("click", () => {
    questions = [];
    answers = new Map();
    index = 0;
    render();
    generate(KIND, { onItems: setQuestions, onStatus: setStatus });
  });
}

/** Show an existing quiz when the notebook opens, without generating one. */
export async function repaintQuiz() {
  questions = [];
  answers = new Map();
  index = 0;
  status = "";

  const set = await resume(KIND, { onItems: setQuestions, onStatus: setStatus });

  if (set && !$("quiz")?.hidden) render();
}

function open() {
  const panel = $("quiz");

  if (panel) panel.hidden = false;

  // The other one closes: the panel holds one at a time now that either fills
  // it, and leaving both open would stack them.
  const other = $("flashcards");

  if (other) other.hidden = true;

  const empty = $("studio-empty");

  if (empty) empty.hidden = true;

  takeOverPanel(true);
  render();
}

function close() {
  const panel = $("quiz");

  if (panel) panel.hidden = true;

  // Back to the tiles. The empty state comes back only if there is nothing to
  // show, which is what it is for.
  takeOverPanel(false);

  const empty = $("studio-empty");

  if (empty) empty.hidden = questions.length > 0;
}

function setQuestions(items) {
  questions = items;

  index = clampIndex(index, questions.length);

  render();
}

function setStatus(next) {
  status = next;
  render();
}

function step(by) {
  if (!questions.length) return;

  index = clampIndex(index + by, questions.length);
  render();
}

function choose(option) {
  // Once, deliberately: changing an answer after seeing it marked would make
  // the score meaningless.
  if (answers.has(index)) return;

  answers.set(index, option);
  render();
}

function render() {
  const panel = $("quiz");

  if (!panel || panel.hidden) return;

  const question = questions[index];
  const body = $("quiz-body");
  const counter = $("quiz-count");
  const scoreEl = $("quiz-score");
  const note = $("quiz-status");

  if (counter) {
    counter.textContent = questions.length ? `${index + 1} / ${questions.length}` : "";
  }

  if (scoreEl) {
    // Out of what has been answered, not out of the deck: the deck is still
    // growing, and "3 / 40" while 37 do not exist yet reads as failure.
    scoreEl.textContent = answers.size
      ? `${scoreAnswers(questions, answers)} / ${answers.size}`
      : "";
  }

  if (note) {
    note.textContent =
      status === "generating"
        ? questions.length
          ? t("studioStillAdding")
          : t("studioWorking")
        : "";
    note.hidden = !note.textContent;
  }

  if (!body) return;

  body.replaceChildren();

  if (!question) {
    body.textContent = status === "generating" ? "" : t("studioNothingYet");
    return;
  }

  const prompt = document.createElement("p");
  prompt.className = "quiz__question";
  prompt.textContent = question.question;
  body.append(prompt);

  const picked = answers.get(index);
  const list = document.createElement("ul");
  list.className = "quiz__options";

  question.options.forEach((option, at) => {
    const item = document.createElement("li");
    const button = document.createElement("button");

    button.type = "button";
    button.className = "quiz__option";

    // A circle before each option, so the question reads as multiple-choice
    // before anything is clicked. Drawn rather than a real radio input: these
    // are buttons that reveal an answer, not a form field, and a radio would
    // promise a submit that does not exist.
    const mark = document.createElement("span");
    mark.className = "quiz__mark";
    mark.setAttribute("aria-hidden", "true");

    const label = document.createElement("span");
    label.className = "quiz__option-text";
    label.textContent = option;

    button.append(mark, label);

    if (picked !== undefined) {
      button.disabled = true;

      // Both are marked, not just the wrong pick: seeing the right answer is
      // the point of taking the quiz.
      if (at === question.answer_index) button.classList.add("is-correct");
      else if (at === picked) button.classList.add("is-wrong");
    } else {
      button.addEventListener("click", () => choose(at));
    }

    item.append(button);
    list.append(item);
  });

  body.append(list);

  const citeButton = $("quiz-cite");

  if (citeButton) citeButton.hidden = !citable(question);
}
