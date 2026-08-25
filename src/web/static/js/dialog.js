// Asking the user something, in the app rather than in the browser.
//
// window.confirm and window.prompt render as "127.0.0.1 says…" — chrome that
// names the host instead of the notebook, ignores the theme, cannot be
// translated, and blocks the page while it is open. These are the same two
// questions asked in the app's own modal, so a rename looks like the rest of
// the interface and a delete can be styled as the destructive thing it is.
//
// Both return a promise: confirm resolves true/false, prompt resolves the
// trimmed text or null if it was dismissed — matching what the natives gave
// back, so the call sites keep the same shape.

import { t } from "./i18n.js";

// One at a time. A second question while the first is unanswered would stack
// backdrops and leave whichever closed last holding the page's focus.
let current = null;

function ask({ title, message, value, confirm, danger = false, input = false }) {
  return new Promise((resolve) => {
    current?.dismiss();

    // So focus goes back where it came from — the row's delete button, the
    // notebook title — rather than to the top of the document.
    const returnTo = document.activeElement;

    const root = document.createElement("div");
    root.className = "modal dialog";

    const backdrop = document.createElement("div");
    backdrop.className = "modal__backdrop";

    const panel = document.createElement("div");
    panel.className = "modal__panel dialog__panel";
    panel.setAttribute("role", "dialog");
    panel.setAttribute("aria-modal", "true");

    const head = document.createElement("header");
    head.className = "modal__head";
    const heading = document.createElement("h2");
    heading.textContent = title;
    head.append(heading);

    const body = document.createElement("div");
    body.className = "modal__body";

    if (message) {
      const text = document.createElement("p");
      text.className = "dialog__message";
      text.textContent = message;
      body.append(text);
    }

    let field = null;
    if (input) {
      field = document.createElement("input");
      field.type = "text";
      field.className = "dialog__input";
      field.value = value ?? "";
      body.append(field);
    }

    const actions = document.createElement("div");
    actions.className = "dialog__actions";

    const cancelBtn = document.createElement("button");
    cancelBtn.type = "button";
    cancelBtn.className = "btn btn--outline";
    cancelBtn.textContent = t("cancel");

    const okBtn = document.createElement("button");
    okBtn.type = "button";
    okBtn.className = `btn ${danger ? "btn--danger" : "btn--dark"}`;
    okBtn.textContent = confirm;

    actions.append(cancelBtn, okBtn);
    body.append(actions);
    panel.append(head, body);
    root.append(backdrop, panel);

    const settle = (result) => {
      if (current !== handle) return;
      current = null;
      document.removeEventListener("keydown", onKey, true);
      root.remove();
      // Only if focus is still inside the dialog — the caller may have moved
      // it somewhere better already.
      if (returnTo?.isConnected) returnTo.focus?.();
      resolve(result);
    };

    const accept = () => {
      if (!input) return settle(true);
      const text = field.value.trim();
      // An empty name is a dismissal, not a rename to nothing.
      settle(text || null);
    };

    const onKey = (event) => {
      if (event.key === "Escape") {
        event.preventDefault();
        settle(input ? null : false);
      } else if (event.key === "Enter" && (input || document.activeElement !== cancelBtn)) {
        event.preventDefault();
        accept();
      }
    };

    // Capture, so Escape closes this rather than whatever is behind it.
    document.addEventListener("keydown", onKey, true);
    backdrop.addEventListener("click", () => settle(input ? null : false));
    cancelBtn.addEventListener("click", () => settle(input ? null : false));
    okBtn.addEventListener("click", accept);

    const handle = { dismiss: () => settle(input ? null : false) };
    current = handle;

    document.body.append(root);

    // The field for a rename (with the old name selected, so typing replaces
    // it); the confirm button otherwise, which is also what Enter would hit.
    if (field) {
      field.focus();
      field.select();
    } else {
      okBtn.focus();
    }
  });
}

/** Yes or no. Resolves true only if the user actually confirmed. */
export const confirmDialog = ({ title, message, confirm, danger = false }) =>
  ask({ title, message, confirm: confirm ?? t("confirm"), danger });

/** Ask for a line of text. Resolves the trimmed value, or null if dismissed. */
export const promptDialog = ({ title, message, value, confirm }) =>
  ask({ title, message, value, confirm: confirm ?? t("save"), input: true });
