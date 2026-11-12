// What the composer accepts as an attached picture. No imports, so it runs
// under `node --test` as well as in the page (see attachment.js).

/** Matches IMAGE_QUERY_MAX_BYTES on the server. */
export const MAX_BYTES = 5 * 1024 * 1024;

export const ACCEPTED = ["image/png", "image/jpeg", "image/webp"];

/** Why *file* cannot be attached, as an i18n key, or null if it can. */
export function rejectionOf(file) {
  if (!file) return "imageWrongType";
  if (!ACCEPTED.includes(file.type)) return "imageWrongType";
  if (file.size > MAX_BYTES) return "imageTooLarge";
  return null;
}

/** The first image among a paste's DataTransferItems or a drop's Files, or null. */
export function firstImage(list) {
  for (const entry of Array.from(list ?? [])) {
    const isItem = typeof entry.getAsFile === "function";
    const file = isItem ? (entry.kind === "file" ? entry.getAsFile() : null) : entry;
    if (file && file.type?.startsWith("image/")) return file;
  }
  return null;
}
