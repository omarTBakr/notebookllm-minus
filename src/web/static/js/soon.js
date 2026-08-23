// One treatment for everything that is planned but not built.
//
// Any element carrying data-soon="Feature name" is muted, badged, and says so
// when clicked. Going through one helper means a feature cannot end up
// half-marked — looking live in one place and disabled in another.

import { t } from "./i18n.js";

export function toast(message, ms = 4000) {
  const host = document.getElementById("toasts");
  if (!host) return;

  const box = document.createElement("div");
  box.className = "toast";
  box.textContent = message;
  host.append(box);

  // Confirmations that fire constantly (a copy, say) pass a shorter life so
  // they do not stack up in the corner.
  setTimeout(() => box.remove(), ms);

  // Returned so a caller that repeats can retire its own previous one.
  return box;
}

/** Tag the Studio cards, where the whole panel is unbuilt.
 *
 * Everything else marked data-soon stays visually quiet and explains itself
 * when clicked — twenty badges at once was unreadable.
 */
function tagStudioCards() {
  document.querySelectorAll(".studio-card[data-soon]").forEach((card) => {
    if (card.querySelector(".soon-tag")) return;
    const tag = document.createElement("span");
    tag.className = "pill soon-tag";
    // Short, so it stays on one line inside the card.
    tag.textContent = t("soon");
    card.append(tag);
  });
}

export function bindComingSoon() {
  tagStudioCards();

  // Capture phase, on the document: the markup is rendered by several modules
  // and some of it arrives later, so a single delegated listener beats
  // wiring each control as it appears.
  document.addEventListener(
    "click",
    (event) => {
      const target = event.target.closest("[data-soon]");
      if (!target) return;

      event.preventDefault();
      event.stopPropagation();

      toast(`${target.dataset.soon} — ${t("comingSoon")}`);
    },
    true
  );
}
