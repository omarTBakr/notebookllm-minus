// A picture attached to the next question: the paperclip button, paste and
// drag-and-drop into the composer, a thumbnail with a remove button, and the
// data URL that goes out with the message.
//
// One picture at a time, and checked here only for what the browser can know
// cheaply (type and size) so a wrong file gets an immediate message. The
// server decodes and re-checks the bytes regardless (routes/chat/messages.py).

import { $ } from "./dom.js";
import { t } from "./i18n.js";
import { toast } from "./soon.js";
import { firstImage, rejectionOf } from "./attachment_rules.js";

let pending = null; // { dataUrl, name }

function readAsDataUrl(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(file);
  });
}

function paint() {
  const tray = $("attachment-tray");
  if (!tray) return;
  tray.replaceChildren();
  tray.hidden = !pending;
  if (!pending) return;

  const thumb = document.createElement("img");
  thumb.className = "attachment__thumb";
  thumb.src = pending.dataUrl;
  thumb.alt = pending.name;

  const remove = document.createElement("button");
  remove.type = "button";
  remove.className = "attachment__remove";
  remove.setAttribute("aria-label", t("removeImage"));
  remove.title = t("removeImage");
  remove.textContent = "×";
  remove.addEventListener("click", () => clear());

  tray.append(thumb, remove);
}

async function attach(file, onChange) {
  const reason = rejectionOf(file);
  if (reason) {
    toast(t(reason));
    return;
  }
  try {
    pending = { dataUrl: await readAsDataUrl(file), name: file.name || "image" };
  } catch {
    toast(t("imageWrongType"));
    return;
  }
  paint();
  onChange?.();
}

/** The attached picture, if any. */
export function current() {
  return pending;
}

/** Forget the attached picture (after sending, or on ×). */
export function clear() {
  pending = null;
  paint();
}

/** Wire the paperclip, paste and drop. *onChange* runs when a picture is attached. */
export function bindAttachment({ onChange } = {}) {
  const button = $("btn-attach");
  const input = $("attach-input");
  const box = document.querySelector(".composer__box");
  if (!button || !input || !box) return;

  button.addEventListener("click", () => input.click());
  input.addEventListener("change", () => {
    const file = input.files?.[0];
    input.value = "";
    if (file) attach(file, onChange);
  });

  // Paste only takes over when the clipboard holds a picture; pasting text
  // into the question behaves exactly as before.
  $("question")?.addEventListener("paste", (event) => {
    const file = firstImage(event.clipboardData?.items);
    if (!file) return;
    event.preventDefault();
    attach(file, onChange);
  });

  box.addEventListener("dragover", (event) => {
    if (Array.from(event.dataTransfer?.types ?? []).includes("Files")) {
      event.preventDefault();
      box.classList.add("is-dropping");
    }
  });
  box.addEventListener("dragleave", () => box.classList.remove("is-dropping"));
  box.addEventListener("drop", (event) => {
    box.classList.remove("is-dropping");
    const file = firstImage(event.dataTransfer?.files);
    if (!file) return;
    event.preventDefault();
    attach(file, onChange);
  });
}
