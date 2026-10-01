// Right panel: the flashcard deck.
//
// One card at a time, front then back, with a link to the page it came from.
// The deck fills while it is being read — the server appends a batch every few
// seconds — so the count moves and "next" keeps working past what existed when
// the panel opened.
//
// Position is held here rather than derived from the item list, because the
// list grows underneath it: recomputing an index from a growing array would
// jump the reader somewhere they were not.

import { $ } from "./dom.js";
import { citable, cite, generate, resume, takeOverPanel } from "./artifacts.js";
import { clampIndex } from "./artifact_items.js";
import { t } from "./i18n.js";
import { toast } from "./soon.js";

const KIND = "flashcards";

let cards = [];
let index = 0;
let flipped = false;
let status = "";

export function bindFlashcards() {
  const tile = document.querySelector(".studio-card--cards");

  tile?.addEventListener("click", async () => {
    open();

    // Show what is already there before making anything. Clicking the tile
    // used to start a generation unconditionally, and starting one *clears
    // the set* — so opening a finished deck wiped it and put the reader back
    // on "reading the documents…" to wait for a replacement they never asked
    // for. Regenerating on purpose is what the "Generate again" button is.
    const existing = await resume(KIND, { onItems: setCards, onStatus: setStatus });

    if (!existing) {
      generate(KIND, { onItems: setCards, onStatus: setStatus });
    }
  });

  $("flashcards-close")?.addEventListener("click", close);
  $("flashcards-flip")?.addEventListener("click", flip);
  $("flashcards-prev")?.addEventListener("click", () => step(-1));
  $("flashcards-next")?.addEventListener("click", () => step(1));
  $("flashcards-cite")?.addEventListener("click", () =>
    // Awaited internally; a failure to open is reported rather than left as
    // an unhandled rejection with a button that silently did nothing.
    cite(cards[index]).catch((e) => toast(e.message)),
  );

  // Regenerate: the deck is replaced server-side, so the local one is dropped
  // rather than left showing cards that no longer exist.
  $("flashcards-again")?.addEventListener("click", () => {
    cards = [];
    index = 0;
    flipped = false;
    render();
    generate(KIND, { onItems: setCards, onStatus: setStatus });
  });
}

/** Show an existing deck when the notebook opens, without generating one. */
export async function repaintFlashcards() {
  cards = [];
  index = 0;
  flipped = false;
  status = "";

  const set = await resume(KIND, { onItems: setCards, onStatus: setStatus });

  if (set && !$("flashcards")?.hidden) render();
}

function open() {
  const panel = $("flashcards");

  if (panel) panel.hidden = false;

  // The other one closes: the panel holds one at a time now that either fills
  // it, and leaving both open would stack them.
  for (const other of ["quiz", "mindmap"]) {
    const el = $(other);

    if (el) el.hidden = true;
  }

  const empty = $("studio-empty");

  if (empty) empty.hidden = true;

  takeOverPanel(true);
  render();
}

function close() {
  const panel = $("flashcards");

  if (panel) panel.hidden = true;

  // Back to the tiles. The empty state comes back only if there is nothing to
  // show, which is what it is for.
  takeOverPanel(false);

  const empty = $("studio-empty");

  if (empty) empty.hidden = cards.length > 0;
}

function setCards(items) {
  cards = items;

  // The reader stays where they are while the deck grows behind them. Only a
  // shrinking deck — a regeneration — moves them, and then to the start.
  index = clampIndex(index, cards.length);

  render();
}

function setStatus(next) {
  status = next;
  render();
}

// The turn, in ms. This covers the horizontal face flip and the two halves of
// the vertical card-to-card transition.
const FLIP_MS = 620;
const CARD_CHANGE_HALF_MS = 310;

let turning = false;

function flip() {
  const face = $("flashcards-face");

  if (!cards.length) return;

  // A second click mid-turn would land the card between its two sides. The
  // guard is cleared by the transition ending, or by a timer in case the
  // transition never runs (reduced motion, a hidden panel, a browser that
  // does not fire it).
  if (turning) return;

  // Keep the same DOM card in place while it turns. Recreating it and adding
  // the final class in the same render frame skips the CSS transition.
  if (!face) return;

  const inner = face.querySelector(".flashcard__inner");
  const from = flipped ? "rotateY(180deg)" : "rotateY(0deg)";
  const to = flipped ? "rotateY(0deg)" : "rotateY(180deg)";

  face.classList.remove("axis-x", "is-changing-card", "is-entering-card");
  face.classList.add("axis-y");
  flipped = !flipped;
  face.classList.toggle("is-flipped", flipped);

  // Web Animations is intentional here. A class-only transition can be
  // coalesced with a render caused by a polling update, leaving the answer in
  // place without ever showing a turn. Animating the element itself gives the
  // button a real 3D turn even in that case; the class remains the final state
  // and the CSS transition is the fallback for older browsers.
  inner?.animate?.(
    [{ transform: from }, { transform: to }],
    { duration: FLIP_MS, easing: "cubic-bezier(0.2, 0.7, 0.3, 1)" },
  );

  turning = true;
  window.setTimeout(() => {
    turning = false;
  }, FLIP_MS);
}

function buildFace(side, body, label) {
  const el = document.createElement("div");
  el.className = `flashcard__face flashcard__face--${side}`;

  const text = document.createElement("p");
  text.className = "flashcard__text";
  text.textContent = body;

  const tag = document.createElement("span");
  tag.className = "flashcard__side";
  tag.textContent = label;

  el.append(text, tag);

  return el;
}

function step(by) {
  if (!cards.length) return;

  const nextIndex = clampIndex(index + by, cards.length);

  if (nextIndex === index || turning) return;

  const face = $("flashcards-face");

  // Moving through the deck is a vertical (top-to-bottom) turn. The outgoing
  // face rotates to its edge, then the incoming card completes the other half
  // of the rotation. This also makes it clear that a new card was selected.
  if (!face) {
    index = nextIndex;
    flipped = false;
    render();
    return;
  }

  turning = true;
  face.classList.remove("axis-x", "axis-y");
  face.classList.add("is-changing-card");

  window.setTimeout(() => {
    index = nextIndex;
    flipped = false;
    render(true);
    window.setTimeout(() => {
      $("flashcards-face")?.classList.remove("is-entering-card");
      turning = false;
    }, CARD_CHANGE_HALF_MS);
  }, CARD_CHANGE_HALF_MS);
}

function render(enteringCard = false) {
  const panel = $("flashcards");

  if (!panel || panel.hidden) return;

  const card = cards[index];
  const face = $("flashcards-face");
  const counter = $("flashcards-count");
  const note = $("flashcards-status");

  if (counter) {
    counter.textContent = cards.length ? `${index + 1} / ${cards.length}` : "";
  }

  if (note) {
    // Says only what is true: still filling, or nothing yet. A finished deck
    // needs no announcement — the cards are the announcement.
    note.textContent =
      status === "generating"
        ? cards.length
          ? t("studioStillAdding")
          : t("studioWorking")
        : "";
    note.hidden = !note.textContent;
  }

  if (!face) return;

  face.classList.remove("is-entering-card");

  face.replaceChildren();

  if (!card) {
    face.classList.remove("is-flipped", "axis-x", "axis-y", "is-changing-card");
    face.textContent = status === "generating" ? "" : t("studioNothingYet");
    return;
  }

  // Both sides exist in the DOM at once and the card turns between them. The
  // earlier version drew one side and swapped the text at the halfway point,
  // which is not a flip — nothing rotates past 90 degrees, so there is no
  // moment where you see the card as a card with two sides.
  const inner = document.createElement("div");
  inner.className = "flashcard__inner";

  inner.append(
    buildFace("front", card.front, t("flashcardFront")),
    buildFace("back", card.back, t("flashcardBack")),
  );

  face.append(inner);

  // The rotation is the state, so it is set from `flipped` on every render
  // rather than toggled — a poll landing mid-deck cannot leave the card
  // showing one side while the class says the other.
  face.classList.remove("axis-x", "is-changing-card");
  face.classList.toggle("is-flipped", flipped);
  if (flipped) face.classList.add("axis-y");
  else face.classList.remove("axis-y");
  if (enteringCard) face.classList.add("is-entering-card");

  const citeButton = $("flashcards-cite");

  if (citeButton) {
    // A card whose document has been deleted since cannot be opened. Hidden
    // rather than disabled: a button that never does anything is noise.
    citeButton.hidden = !citable(card);
  }
}
