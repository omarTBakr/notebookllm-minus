// The sign-in / sign-up form's logic, with no DOM and no imports so it can be
// tested under node (tests/frontend/auth_form.test.mjs).

// The localStorage key the old profile picker kept the profile id under. A
// browser that still has one can claim that profile, and its notebooks, the
// first time it signs up.
export const LEGACY_USER_KEY = "notebookllm.user_id";

export const MIN_PASSWORD = 8;

const EMAIL = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;

/** The first problem with the form, as an i18n key, or null when it is fine. */
export function validate(mode, { email, password, confirm }) {
  if (!EMAIL.test((email ?? "").trim())) return "authBadEmail";
  if (!password) return "authNoPassword";
  if (mode === "signup") {
    if (password.length < MIN_PASSWORD) return "authShortPassword";
    if (password !== confirm) return "authPasswordMismatch";
  }
  return null;
}

/** The endpoint and JSON body for a submit. */
export function payload(mode, { email, password, displayName }, legacyUserId = null) {
  const base = { email: (email ?? "").trim(), password };
  if (mode !== "signup") return { url: "/auth/login", body: base };

  const body = { ...base, display_name: (displayName ?? "").trim() };
  if (legacyUserId) body.claim_user_id = legacyUserId;
  return { url: "/auth/signup", body };
}

/** Where to go after signing in: back to the page that sent us, if it was ours. */
export function nextUrl(search) {
  const next = new URLSearchParams(search ?? "").get("next");
  // Only a local path: an absolute or protocol-relative URL here would make
  // the login page an open redirect.
  return next && next.startsWith("/") && !next.startsWith("//") ? next : "/";
}
