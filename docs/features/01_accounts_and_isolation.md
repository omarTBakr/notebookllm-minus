# Accounts and per-user isolation

## What it does

Puts email and password sign-in in front of the whole app, and makes every request answer only
for the signed-in user's own data. A notebook, source, task or message that belongs to someone
else looks exactly like one that does not exist.

## How it works

- **Sign-in** (`src/application/services/account/AuthService.py`). The browser holds a random
  256-bit token in an HttpOnly cookie; only its sha256 is stored, so a copy of the
  `auth_sessions` table cannot be replayed as cookies. Passwords are hashed with argon2id and
  rehashed on login when the recommended parameters change. A login for an unknown email still
  runs a full verify, so response time does not reveal which emails have accounts, and the error
  never says which of email or password was wrong. Repeated failures lock that email for a while.
- **Sessions slide.** A session lasts `AUTH_SESSION_DAYS` since the last request. Expired
  sign-ins are refused when presented and removed by the maintenance sweep.
- **Ownership** (`services/account/AccessService.py`). Every id a route takes resolves to one
  owning user: a session, a chat, a project (a chat id *is* a project id), a task. A missing
  resource and someone else's raise the same 404.
- **Enforced on the routers, not per route** (`src/presentation/auth.py`). `require_user` and
  `enforce_ownership` are router-level dependencies on `/chat`, `/data`, `/process` and `/nlp`,
  so a route added later is covered without anyone remembering to add a check.
  `tests/api/test_isolation.py` walks `app.routes` to prove it.
- **A cookie, not a bearer header**: PDF embeds and downloads are plain same-origin GETs the
  browser makes on its own, and only a cookie rides along with those. `OriginCheckMiddleware`
  (`src/presentation/middleware/origin_check.py`) checks state-changing requests against the
  page's origin.
- **Legacy profiles.** A profile created before accounts existed can be claimed exactly once, at
  signup, by naming its id. One that already has credentials can never be claimed.
- **Deleting an account** (`UserService`) removes its notebooks, sources, chunks and vectors,
  derived-first.

## Settings

| Setting | Does what |
| --- | --- |
| `AUTH_COOKIE_NAME` | the session cookie's name |
| `AUTH_COOKIE_SECURE` | set once the app is served over HTTPS |
| `AUTH_SESSION_DAYS` | sliding session lifetime |
| `AUTH_PASSWORD_MIN_LENGTH` | shortest accepted password |
| `AUTH_LOGIN_MAX_FAILURES` / `AUTH_LOGIN_LOCKOUT_MINUTES` | failed logins before an email is locked, and for how long |
| `DOCS_ENABLED` | `/docs`, `/redoc`, `/openapi.json`; off in production |

## API

| Method | Path | |
| --- | --- | --- |
| `POST` | `/auth/signup` | create an account (optionally claiming a legacy profile) |
| `POST` | `/auth/login` · `/auth/logout` · `/auth/logout-all` | sign in, sign out here, sign out everywhere |
| `GET` | `/auth/me` | the signed-in user |
| `POST` | `/auth/password` | change the password; ends the other sessions |
| `GET` | `/login` | the sign-in page |

## Data

`users` (email, password hash) and `auth_sessions` (token hash, expiry), added by migration `0013`.

## Tests

`tests/api/test_auth.py`, `test_isolation.py`, `test_users.py`, `tests/frontend/auth_form.test.mjs`.

## Diagrams

[Sign up, sign in, sign out](../../software_engineering/3_sequence_diagrams/signup_login.png) · [Every request: session, then ownership](../../software_engineering/3_sequence_diagrams/request_authorization.png) ·
[class diagram](../../software_engineering/4_class_diagrams/accounts.png) · [use cases](../../software_engineering/2_use_case_diagrams/accounts.png) · [user stories](../../software_engineering/1_user_stories/07_accounts_and_privacy.png)

## Known limits

- The login lockout counter lives in the API process, which is right while the API is one
  uvicorn process and wrong the day it is several.
- No email verification or password reset by email.
