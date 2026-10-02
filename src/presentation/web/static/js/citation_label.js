// How a citation names where in its source it came from, with no DOM and no imports.
//
// Separated for the reason progress.js is: the label rules are the part worth
// testing, and a module that imports nothing can be loaded under `node --test`.

/** The text of a citation's link, e.g. `report.pdf · p. 4`, `Sheet1 · row 12`.
 *
 *  `t` is the translator, passed in so this stays import-free.
 *
 *  - video: the moment it is spoken
 *  - spreadsheet row: the sheet (when the file has several) and the row as the
 *    spreadsheet application numbers it -- a row is not a page
 *  - PDF: its page
 *  - anything else: just the source's name
 */
export function citationLabel(cite, t) {
  if (cite.time_label) return `${cite.source} · ${cite.time_label}`;

  if (cite.row !== null && cite.row !== undefined) {
    const where = cite.sheet ? `${cite.sheet} · ${t("row")} ${cite.row}` : `${t("row")} ${cite.row}`;
    return `${cite.source} · ${where}`;
  }

  if (cite.page_label) return `${cite.source} · ${t("page")} ${cite.page_label}`;

  return cite.source;
}

/** The tooltip on that link: what clicking it will do. */
export function citationTitle(cite, t) {
  if (cite.time_label) return t("openAtMoment");
  if (cite.row !== null && cite.row !== undefined) return t("openAtRow");
  return cite.page_number ? t("openAtPage") : t("openSource");
}
