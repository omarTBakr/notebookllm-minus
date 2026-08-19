// The two model pickers. Options come from GET /chat/models, so the list is
// whatever Ollama actually has pulled — nothing here names a model.

import { api } from "./api.js";
import { t } from "./i18n.js";
import { state } from "./state.js";

let catalogue = null;

const $ = (id) => document.getElementById(id);

function fill(select, options, selected) {
  select.replaceChildren();

  for (const model of options) {
    const option = document.createElement("option");
    option.value = model.id;
    option.textContent = model.dimensions
      ? `${model.id} · ${model.dimensions}d`
      : model.id;
    option.selected = model.id === selected;
    select.append(option);
  }

  // The chat's model may not be in the list if it was pulled then removed;
  // show it anyway so the picker reflects reality rather than silently
  // appearing to be set to something else.
  if (selected && !options.some((m) => m.id === selected)) {
    const option = document.createElement("option");
    option.value = selected;
    option.textContent = `${selected} (missing)`;
    option.selected = true;
    select.prepend(option);
  }
}

export async function loadCatalogue() {
  try {
    catalogue = await api.listModels();
  } catch (error) {
    $("model-status").textContent = error.message;
  }
}

/** Show the pickers for the active chat. */
export function showFor(chat) {
  const bar = $("modelbar");

  if (!chat || !catalogue) {
    bar.hidden = true;
    return;
  }

  bar.hidden = false;

  fill($("model-chat"), catalogue.chat, chat.generation_model);
  fill($("model-embed"), catalogue.embedding, chat.embedding_model);

  $("model-chat").disabled = false;
  $("model-embed").disabled = false;
  $("model-status").textContent = "";
}

async function apply(patch, { rebuilds } = {}) {
  if (!state.activeChat) return;

  const status = $("model-status");
  const selects = [$("model-chat"), $("model-embed")];

  // Changing the embedding model re-embeds every chunk, which is slow enough
  // that the controls have to be locked while it runs.
  selects.forEach((s) => (s.disabled = true));
  status.textContent = rebuilds ? t("reindexing") : "…";

  try {
    const result = await api.setModels(state.activeChat.chat_id, patch);

    state.activeChat.generation_model = result.generation_model;
    state.activeChat.embedding_model = result.embedding_model;
    state.activeChat.embedding_dimensions = result.embedding_dimensions;

    status.textContent = result.reindexed_chunks
      ? `${t("reindexed")} ${result.reindexed_chunks} ${t("chunks")}`
      : t("modelSaved");
  } catch (error) {
    status.textContent = error.message;
    // Put the pickers back to what the server actually holds.
    showFor(state.activeChat);
  } finally {
    selects.forEach((s) => (s.disabled = false));
  }
}

export function bindModelPickers() {
  $("model-chat").addEventListener("change", (event) =>
    apply({ generation_model: event.target.value })
  );

  $("model-embed").addEventListener("change", (event) =>
    apply({ embedding_model: event.target.value }, { rebuilds: true })
  );
}
