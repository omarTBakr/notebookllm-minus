// What the composer accepts as an attached picture.
//
//     node --test tests/frontend/*.test.mjs

import test from "node:test";
import assert from "node:assert/strict";

import { MAX_BYTES, firstImage, rejectionOf } from "../../src/presentation/web/static/js/attachment_rules.js";

test("png, jpeg and webp under the limit are accepted", () => {
  for (const type of ["image/png", "image/jpeg", "image/webp"]) {
    assert.equal(rejectionOf({ type, size: 1000 }), null);
  }
});

test("other types are refused, gif and pdf included", () => {
  assert.equal(rejectionOf({ type: "image/gif", size: 10 }), "imageWrongType");
  assert.equal(rejectionOf({ type: "application/pdf", size: 10 }), "imageWrongType");
  assert.equal(rejectionOf(null), "imageWrongType");
});

test("a picture over the server's limit is refused before it is sent", () => {
  assert.equal(rejectionOf({ type: "image/png", size: MAX_BYTES + 1 }), "imageTooLarge");
  assert.equal(rejectionOf({ type: "image/png", size: MAX_BYTES }), null);
});

test("a paste takes the first image item and ignores text", () => {
  const picture = { type: "image/png", name: "p.png" };
  const items = [
    { kind: "string", type: "text/plain", getAsFile: () => null },
    { kind: "file", type: "image/png", getAsFile: () => picture },
  ];
  assert.equal(firstImage(items), picture);
});

test("a drop of files finds the image among them, or nothing", () => {
  const doc = { type: "application/pdf" };
  const jpeg = { type: "image/jpeg" };
  assert.equal(firstImage([doc, jpeg]), jpeg);
  assert.equal(firstImage([doc]), null);
  assert.equal(firstImage(undefined), null);
});
