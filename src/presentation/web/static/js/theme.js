// Light / dark / system, remembered across reloads.
//
// "system" is the absence of a choice: the attribute comes off and the media
// query in base.css takes over. Anything else stamps data-theme on the root,
// which beats the media query in both directions — so Light stays light on a
// machine set to dark.

const KEY = "notebookllm.theme";

export const CHOICES = ["light", "dark", "system"];

export function currentTheme() {
  return localStorage.getItem(KEY) ?? "system";
}

export function applyTheme(choice) {
  const value = CHOICES.includes(choice) ? choice : "system";

  if (value === "system") {
    document.documentElement.removeAttribute("data-theme");
    localStorage.removeItem(KEY);
  } else {
    document.documentElement.dataset.theme = value;
    localStorage.setItem(KEY, value);
  }

  document.querySelectorAll("[data-theme-choice]").forEach((btn) => {
    btn.classList.toggle("is-active", btn.dataset.themeChoice === value);
  });
}

export function bindTheme() {
  document.querySelectorAll("[data-theme-choice]").forEach((btn) => {
    btn.addEventListener("click", () => applyTheme(btn.dataset.themeChoice));
  });

  applyTheme(currentTheme());
}
