// The avatar menu: the signed-in account, and what you can do with it.
// Who you are comes from the session cookie (GET /auth/me), never from
// anything the browser stores.

import { confirmDialog, promptDialog } from "./dialog.js";
import { api } from "./api.js";
import { t } from "./i18n.js";
import { toast } from "./soon.js";
import { state } from "./state.js";
import { $ } from "./dom.js";

const initial = (label) => (label ?? "").trim().charAt(0).toUpperCase() || "?";

function paintAvatar() {
  $("avatar-initial").textContent = initial(state.userLabel);
  $("btn-profile").title = state.userEmail ? `${state.userLabel} · ${state.userEmail}` : state.userLabel ?? "";
  $("account-name").textContent = state.userLabel ?? "";
  $("account-email").textContent = state.userEmail ?? "";
}

function closeMenu() {
  $("profile-menu").hidden = true;
  $("btn-profile").setAttribute("aria-expanded", "false");
}

/** Load the signed-in user. A 401 is handled by api.js (back to /login). */
export async function initProfile() {
  const { user } = await api.me();
  state.userId = user.user_id;
  state.userLabel = user.label;
  state.userEmail = user.email;
  paintAvatar();
}

async function renameCurrent() {
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
  } catch (error) {
    toast(error.message);
  }
}

async function changePassword() {
  closeMenu();
  const current = await promptDialog({ title: t("changePassword"), message: t("currentPasswordPrompt"), secret: true });
  if (!current) return;
  const next = await promptDialog({ title: t("changePassword"), message: t("newPasswordPrompt"), secret: true });
  if (!next) return;

  try {
    await api.changePassword(current, next);
    toast(t("passwordChanged"));
  } catch (error) {
    toast(error.message);
  }
}

async function signOut() {
  try {
    await api.logout();
  } finally {
    window.location.replace("/login");
  }
}

async function signOutEverywhere() {
  try {
    await api.logoutAll();
  } finally {
    window.location.replace("/login");
  }
}

/** Delete the account and everything under it, then back to the login page. */
async function deleteCurrent() {
  const name = state.userEmail || state.userLabel || "";
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

  window.location.replace("/login");
}

export function bindProfile() {
  $("btn-profile").addEventListener("click", (event) => {
    event.stopPropagation();
    const menu = $("profile-menu");
    menu.hidden = !menu.hidden;
    $("btn-profile").setAttribute("aria-expanded", String(!menu.hidden));
  });

  $("btn-rename-profile").addEventListener("click", renameCurrent);
  $("btn-change-password").addEventListener("click", changePassword);
  $("btn-sign-out").addEventListener("click", signOut);
  $("btn-sign-out-all").addEventListener("click", signOutEverywhere);
  $("btn-delete-profile").addEventListener("click", deleteCurrent);

  document.addEventListener("click", (event) => {
    if (!$("profile-menu").hidden && !event.target.closest("#profile-menu")) closeMenu();
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") closeMenu();
  });
}
