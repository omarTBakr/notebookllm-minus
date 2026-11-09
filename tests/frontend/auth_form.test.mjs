// The login page's form logic.
//
//     node --test tests/frontend/*.test.mjs

import test from "node:test";
import assert from "node:assert/strict";

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const source = readFileSync(resolve(here, "../../src/presentation/web/static/js/auth_form.js"), "utf8");

const { validate, payload, nextUrl } = await import(
  `data:text/javascript;base64,${Buffer.from(source).toString("base64")}`
);

const good = { email: "ada@example.com", password: "long enough pw", confirm: "long enough pw" };

test("a complete sign-up passes", () => {
  assert.equal(validate("signup", good), null);
});

test("a bad email is caught first", () => {
  assert.equal(validate("signin", { ...good, email: "ada" }), "authBadEmail");
});

test("sign-up needs a long password and a matching confirmation", () => {
  assert.equal(validate("signup", { ...good, password: "short", confirm: "short" }), "authShortPassword");
  assert.equal(validate("signup", { ...good, confirm: "something else" }), "authPasswordMismatch");
});

test("sign-in does not judge the password's length", () => {
  assert.equal(validate("signin", { email: good.email, password: "x" }), null);
});

test("sign-in posts email and password only", () => {
  assert.deepEqual(payload("signin", { ...good, displayName: "Ada" }), {
    url: "/auth/login",
    body: { email: "ada@example.com", password: "long enough pw" },
  });
});

test("sign-up claims the legacy profile when there is one", () => {
  const { url, body } = payload("signup", { ...good, displayName: " Ada " }, "old-profile-id");
  assert.equal(url, "/auth/signup");
  assert.equal(body.display_name, "Ada");
  assert.equal(body.claim_user_id, "old-profile-id");
  assert.equal(payload("signup", good, null).body.claim_user_id, undefined);
});

test("next is followed only to a local path", () => {
  assert.equal(nextUrl("?next=%2F%3Fnotebook%3Dc1"), "/?notebook=c1");
  assert.equal(nextUrl("?next=https://evil.example"), "/");
  assert.equal(nextUrl("?next=//evil.example"), "/");
  assert.equal(nextUrl(""), "/");
});
