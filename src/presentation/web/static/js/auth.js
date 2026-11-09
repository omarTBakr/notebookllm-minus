// The login page: sign in, or create an account.

import { LEGACY_USER_KEY, nextUrl, payload, validate } from "./auth_form.js";
import { applyLang, t } from "./i18n.js";

const $ = (id) => document.getElementById(id);

let mode = "signin";

function legacyUserId() {
  try {
    return localStorage.getItem(LEGACY_USER_KEY);
  } catch {
    return null;
  }
}

function setMode(next) {
  mode = next;
  const signup = mode === "signup";

  for (const tab of document.querySelectorAll("[data-auth-mode]")) {
    const active = tab.dataset.authMode === mode;
    tab.classList.toggle("is-active", active);
    tab.setAttribute("aria-selected", String(active));
  }

  for (const el of document.querySelectorAll("[data-signup-only]")) el.hidden = !signup;

  $("auth-password").autocomplete = signup ? "new-password" : "current-password";
  $("auth-submit").textContent = t(signup ? "signUp" : "signIn");
  // Only worth saying when there is something to bring along.
  $("auth-claim-note").hidden = !(signup && legacyUserId());
  showError(null);
}

function showError(message) {
  const box = $("auth-error");
  box.textContent = message ?? "";
  box.hidden = !message;
}

async function submit(event) {
  event.preventDefault();

  const form = {
    email: $("auth-email").value,
    password: $("auth-password").value,
    confirm: $("auth-confirm").value,
    displayName: $("auth-name").value,
  };

  const problem = validate(mode, form);
  if (problem) return showError(t(problem));

  const legacy = mode === "signup" ? legacyUserId() : null;
  const { url, body } = payload(mode, form, legacy);

  const button = $("auth-submit");
  button.disabled = true;
  showError(null);

  try {
    const response = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });

    if (!response.ok) {
      let detail = `${response.status} ${response.statusText}`;
      try {
        detail = (await response.json()).detail ?? detail;
      } catch { /* not JSON */ }
      return showError(detail);
    }

    // The profile is now an account (or was never claimed): the old id has
    // done its job either way.
    if (legacy) {
      try { localStorage.removeItem(LEGACY_USER_KEY); } catch { /* ignore */ }
    }

    window.location.replace(nextUrl(window.location.search));
  } catch (error) {
    showError(error.message);
  } finally {
    button.disabled = false;
  }
}

function main() {
  let stored = null;
  try { stored = localStorage.getItem("lang"); } catch { /* ignore */ }
  applyLang(stored ?? document.body.dataset.defaultLang ?? "en");

  for (const tab of document.querySelectorAll("[data-auth-mode]")) {
    tab.addEventListener("click", () => setMode(tab.dataset.authMode));
  }

  for (const button of document.querySelectorAll("[data-lang]")) {
    button.addEventListener("click", () => {
      applyLang(button.dataset.lang);
      try { localStorage.setItem("lang", button.dataset.lang); } catch { /* ignore */ }
      setMode(mode);
    });
  }

  $("auth-form").addEventListener("submit", submit);

  // A browser that used the old profile picker most likely needs an account,
  // not a sign-in: start it on the sign-up tab.
  setMode(legacyUserId() ? "signup" : "signin");
  $("auth-email").focus();
}

main();
