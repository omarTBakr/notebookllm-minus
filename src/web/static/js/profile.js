// The avatar menu: which profile you are, and every profile on this install.
// No authentication — a picker, not a login.

import { confirmDialog, promptDialog } from "./dialog.js";
import { api } from "./api.js";
import { t } from "./i18n.js";
import { toast } from "./soon.js";
import { forgetUser, rememberUser, state, storedUserId } from "./state.js";
import { $ } from "./dom.js";

let onSwitch = () => {};

export function bindProfileSwitch(handler) {
  onSwitch = handler;
}

const initial = (label) => (label ?? "").trim().charAt(0).toUpperCase() || "?";

/** Repaint the avatar after the profile changed from outside this module. */
export function adoptProfile() {
  paintAvatar();
}

function paintAvatar() {
  $("avatar-initial").textContent = initial(state.userLabel);
  $("btn-profile").title = state.userLabel ?? "";
}

function closeMenu() {
  $("profile-menu").hidden = true;
  $("btn-profile").setAttribute("aria-expanded", "false");
}

async function renderList() {
  const host = $("profile-list");
  host.replaceChildren();

  try {
    const { users } = await api.listUsers();

    for (const user of users) {
      const item = document.createElement("button");
      item.className = "dropdown__item";
      if (user.user_id === state.userId) item.classList.add("is-active");
      item.title = user.label;

      const dot = document.createElement("span");
      dot.className = "dot";
      dot.textContent = initial(user.label);

      const name = document.createElement("span");
      name.textContent = user.label;

      item.append(dot, name);
      item.addEventListener("click", () => {
        if (user.user_id === state.userId) return;
        select(user);
      });

      host.append(item);
    }
  } catch (error) {
    const failed = document.createElement("p");
    failed.className = "field__hint";
    failed.textContent = error.message;
    host.append(failed);
  }
}

function select(user) {
  state.userId = user.user_id;
  state.userLabel = user.label;
  rememberUser(user.user_id);
  paintAvatar();
  closeMenu();
  onSwitch();
}

/** Resolve identity on load: reuse the stored profile, or make the first one. */
export async function initProfile() {
  const stored = storedUserId();

  if (stored) {
    try {
      const user = await api.getUser(stored);
      state.userId = user.user_id;
      state.userLabel = user.label;
      paintAvatar();
      return;
    } catch (error) {
      // A 404 is routine — the id outlived the database, which is exactly
      // what a fresh install looks like. Make a new profile instead.
      if (error.status !== 404) throw error;
      forgetUser();
    }
  }

  await createProfile();
}

export async function createProfile() {
  const user = await api.createUser();

  state.userId = user.user_id;
  state.userLabel = user.label;
  rememberUser(user.user_id);
  paintAvatar();

  state.notebooks = [];
  state.notebook = null;
  state.sources = [];

  return user;
}

async function renameCurrent() {
  if (!state.userId) return;

  const label = await promptDialog({
    title: t("rename"),
    message: t("renamePrompt"),
    value: state.userLabel ?? "",
  });
  if (!label) return;

  try {
    const result = await api.renameUser(state.userId, label);
    state.userLabel = result.label;
    paintAvatar();
    await renderList();
  } catch (error) {
    toast(error.message);
  }
}

/** Delete this profile and everything under it, then land somewhere valid. */
async function deleteCurrent() {
  if (!state.userId) return;

  const name = state.userLabel || state.userId.slice(0, 8);
  const ok = await confirmDialog({
    title: t("deleteProfile"),
    message: t("confirmDeleteProfile").replace("{name}", name),
    confirm: t("deleteProfile"),
    danger: true,
  });
  if (!ok) return;

  try {
    await api.deleteUser(state.userId);
  } catch (error) {
    toast(error.message);
    return;
  }

  // The stored id now points at nothing, which is the one state initProfile is
  // written to recover from — so drop it and let it choose or mint the next
  // profile rather than deciding here.
  forgetUser();
  state.userId = null;
  state.userLabel = null;

  closeMenu();
  toast(t("profileDeleted").replace("{name}", name));
  onSwitch();
}

export function bindProfile() {
  $("btn-profile").addEventListener("click", (event) => {
    event.stopPropagation();
    const menu = $("profile-menu");
    menu.hidden = !menu.hidden;
    $("btn-profile").setAttribute("aria-expanded", String(!menu.hidden));
    if (!menu.hidden) renderList();
  });

  $("btn-rename-profile").addEventListener("click", renameCurrent);
  $("btn-delete-profile").addEventListener("click", deleteCurrent);

  $("btn-new-profile").addEventListener("click", async () => {
    try {
      await createProfile();
      closeMenu();
      onSwitch();
    } catch (error) {
      toast(error.message);
    }
  });

  document.addEventListener("click", (event) => {
    if (!$("profile-menu").hidden && !event.target.closest("#profile-menu")) closeMenu();
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") closeMenu();
  });
}
