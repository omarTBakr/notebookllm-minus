// Bootstrap: load the logo, wire the panels together, open a notebook.

import {
  ask,
  bindAnswerActions,
  bindCitationClick,
  bindTranscript,
  loadHistory,
  stopAnswer,
} from "./chat.js";
import { applyLang, t } from "./i18n.js";
import {
  bindMemoryButton,
  bindMemorySuggestion,
  composeMemoryText,
  confirmMemorySave,
  isMemoryActive,
  isMemoryCommand,
  resetMemoryMode,
  watchForMemoryReply,
} from "./memory.js";
import {
  bindNotebookOpen,
  bindNotebooks,
  create as createNotebook,
  initialNotebookId,
  loadList,
  paintMeta,
  paintTitle,
  renderList,
} from "./notebooks.js";
import { api } from "./api.js";
import { adoptProfile, bindProfile, bindProfileSwitch, initProfile } from "./profile.js";
import {
  bindLangChange,
  bindSettings,
  loadCatalogue,
  showBackend,
  showFor,
} from "./settings.js";
import { bindPanels, repaint as repaintPanels, reveal as revealPanel } from "./panels.js";
import {
  bindRevealPanel,
  bindSources,
  bindSourcesChanged,
  load as loadSources,
  openAt as openSourceAt,
  saveAnswer as saveAnswerToSources,
} from "./sources.js";
import { bindStudio, repaint as repaintStudio } from "./studio.js";
import { bindFlashcards, repaintFlashcards } from "./flashcards.js";
import { bindMindMap, repaintMindMap } from "./mindmap.js";
import { bindQuiz, repaintQuiz } from "./quiz.js";
import { bindTheme } from "./theme.js";
import { bindAutoCopy, copyText } from "./clipboard.js";
import { bindComingSoon, toast } from "./soon.js";
import { rememberNotebook, rememberUser, state, storedLang } from "./state.js";
import { $ } from "./dom.js";

// --- opening a notebook -------------------------------------------------------

async function openNotebook(chatId) {
  if (!chatId) {
    state.notebook = null;
    state.sources = [];
    paintTitle();
    paintMeta();
    showFor(null);
    $("question").disabled = true;
    $("btn-send").disabled = true;
    return;
  }

  state.notebook = await api.getNotebook(chatId);
  rememberNotebook(chatId);

  // Following a link to someone else's notebook switches to that profile —
  // there is no auth, and showing a notebook while claiming to be a profile
  // that does not own it would be a lie about whose list you are looking at.
  if (state.notebook.user_id && state.notebook.user_id !== state.userId) {
    const user = await api.getUser(state.notebook.user_id);
    state.userId = user.user_id;
    state.userLabel = user.label;
    rememberUser(user.user_id);
    adoptProfile();
    await loadList();
  }

  paintTitle();
  renderList();

  await Promise.all([loadSources(chatId), loadHistory(chatId)]);

  // Derived, not latched. This used to be add()-only, so the first question
  // hid the hero for the rest of the session — every notebook opened after
  // that showed an empty column where its title should be.
  paintHero();

  paintMeta();
  showFor(state.notebook);

  // Not awaited: an existing deck, quiz or map appears when it appears, and a
  // notebook that has never generated one answers exists:false anyway. Making
  // the notebook wait on two more round trips to show nothing would be a poor
  // trade.
  repaintFlashcards();
  repaintQuiz();
  repaintMindMap();

  $("question").disabled = false;
  $("btn-send").disabled = false;
}

/** Load everything belonging to the current profile. */
async function enterProfile() {
  await loadList();

  // A profile with no notebooks has nothing to type into, so give it one
  // rather than an empty workspace.
  if (!state.notebooks.length) {
    await createNotebook();
    return;
  }

  await openNotebook(initialNotebookId());
}

// --- composer -----------------------------------------------------------------

/** The hero belongs above an empty transcript and nowhere else. */
function paintHero() {
  const empty = $("messages").childElementCount === 0;
  $("hero").classList.toggle("is-hidden", !empty);
}

function autoGrow(box) {
  box.style.height = "auto";
  box.style.height = `${Math.min(box.scrollHeight, 160)}px`;
}

/** The send button doubles as Stop while an answer is streaming.
 *
 * One button rather than two: the only thing worth doing mid-answer is
 * stopping it, and a second control would sit disabled the rest of the time.
 */
function paintSendButton(streaming) {
  const btn = $("btn-send");

  btn.classList.toggle("composer__send--stop", streaming);
  btn.setAttribute("aria-label", streaming ? t("stop") : "send");
  btn.title = streaming ? t("stop") : "";
  btn.querySelector("use").setAttribute("href", streaming ? "#i-stop" : "#i-send");
}

async function send() {
  const box = $("question");

  // Mid-answer the same button means Stop. Checked before the empty-text
  // guard below, which would otherwise swallow the click.
  if (state.streaming) {
    stopAnswer();
    return;
  }

  const draft = box.value.trim();
  if (!draft) return;

  if (!state.notebook) {
    toast(t("noNotebookYet"));
    return;
  }

  // Active /memory mode means the box holds only the fact -- the "/memory"
  // prefix itself was already stripped out by the chip (see memory.js) and
  // has to be reconstructed for the message actually sent. isMemoryCommand
  // is the fallback for a literal "/memory ..." draft that reached here
  // without going through that mode at all.
  const activeMemory = isMemoryActive();
  const memoryText = activeMemory ? draft : isMemoryCommand(draft);
  const text = activeMemory ? composeMemoryText(draft) : draft;

  // A `/memory` message gets a confirmation instead of going straight out —
  // the one place this composer asks before sending. Declining leaves the
  // text in the box untouched, same as never having pressed Enter.
  if (memoryText !== null && !(await confirmMemorySave(memoryText))) return;

  box.value = "";
  autoGrow(box);
  // memory.js's own "input" listener reacts to this; harmless once
  // resetMemoryMode below has already run, kept for any other listener.
  box.dispatchEvent(new Event("input"));
  resetMemoryMode();

  // Deliberately *not* disabled: it is the Stop button now, and disabling it
  // would leave no way to interrupt a long answer. Passed explicitly because
  // ask() has not run yet, so state.streaming is still false here.
  paintSendButton(true);

  // The hero only makes sense above an empty transcript.
  paintHero();

  const chatId = state.notebook.chat_id;

  await ask(chatId, text, {
    isCommand: memoryText !== null,
    onDone: async () => {
      // Back to an arrow before the three awaits below, or the button would
      // sit showing Stop for the length of them.
      paintSendButton(false);
      // The first question renames the notebook server-side.
      state.notebook = await api.getNotebook(chatId);
      paintTitle();
      await loadList();

      // The ack just rendered is not the real answer — that lands later,
      // written by the background worker. Not awaited: this keeps polling
      // after send() has returned, same as the answer itself keeps streaming
      // in the background from the reader's point of view.
      if (memoryText !== null) watchForMemoryReply(chatId);
    },
  });
}

function bindComposer() {
  $("btn-send").addEventListener("click", send);
  bindMemorySuggestion();
  bindMemoryButton();

  const box = $("question");
  box.addEventListener("input", () => autoGrow(box));
  box.addEventListener("keydown", (event) => {
    // Enter sends; Shift+Enter is a newline — the convention every chat uses.
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      send();
    }
  });
}

// --- boot ---------------------------------------------------------------------

async function main() {
  applyLang(storedLang() ?? document.body.dataset.defaultLang ?? "en");

  bindTheme();
  bindComingSoon();
  bindComposer();
  bindPanels();
  bindNotebooks();
  bindProfile();
  bindSettings();
  bindSources();
  bindStudio();
  bindFlashcards();
  bindQuiz();
  bindMindMap();
  bindAutoCopy();
  bindTranscript();

  bindNotebookOpen((chatId) => openNotebook(chatId).catch((e) => toast(e.message)));
  bindProfileSwitch(() => enterProfile().catch((e) => toast(e.message)));
  bindSourcesChanged(() => paintMeta());
  // Clicking a citation opens that document at the cited page. Wired here so
  // chat.js and sources.js never import each other.
  bindCitationClick((assetId, pageNumber, chunkOrder) =>
    openSourceAt(assetId, pageNumber, chunkOrder).catch((e) => toast(e.message)),
  );
  bindRevealPanel(revealPanel);
  // The answer's own markdown, handed to whichever module owns the action.
  bindAnswerActions({
    copy: (markdown) => copyText(markdown),
    save: (markdown) => saveAnswerToSources(markdown).catch((err) => toast(err.message)),
  });
  bindLangChange(() => {
    // Labels changed, so anything rendered from them has to be redrawn.
    // applyLang only repaints [data-i18n] elements, and the model pickers are
    // built in JS — their badges would keep the old language otherwise.
    renderList();
    paintTitle();
    paintMeta();
    repaintStudio();
    repaintPanels();
    showFor(state.notebook);
  });

  // Not awaited: building this list makes the server ask every model whether
  // it can embed, which loads them. Nothing on the first screen needs it —
  // only the settings dialog does — so let it arrive when it arrives rather
  // than holding the whole app behind it.
  loadCatalogue();

  try {
    await initProfile();
    await enterProfile();
  } catch (error) {
    toast(error.message);
  }

  showBackend();
}

main();
