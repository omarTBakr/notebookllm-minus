// What to do with one reading of a source's progress, with no DOM and no imports.
//
// Separated for the reason artifact_items.js is: a module with no dependencies
// can be loaded under `node --test`, and this is the one decision in the
// progress bar that is easy to get wrong -- stopping at the end of the *first*
// task of a source that takes two.

/** The step after one progress reading.
 *
 *  - fail    the task failed: reject with its error
 *  - follow  the task finished but handed on to another (a link is fetched by
 *            one task and ingested by the next): keep polling `taskId`
 *  - done    nothing more is coming
 *  - wait    still running: paint it and poll again
 */
export function nextStep(progress) {
  if (progress.status === "FAILURE") return { kind: "fail" };

  if (!progress.active) {
    return progress.next_task_id
      ? { kind: "follow", taskId: progress.next_task_id }
      : { kind: "done" };
  }

  return { kind: "wait" };
}
