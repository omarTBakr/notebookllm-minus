// The one DOM helper every view module needs.
//
// This was seven identical copies, one per module that touches the page.

/** The element with this id, or null. */
export const $ = (id) => document.getElementById(id);
