"""Accounts: signup, login, the session cookie behind them, and logout.

Identity was an opaque profile id picked from a list. It is now an email and a
password, and every request is tied to one user by a session cookie:

* the cookie carries a random 256-bit token; only its sha256 is stored, so a
  copy of the auth_sessions table cannot be replayed as cookies;
* passwords are hashed with argon2id (argon2-cffi), and rehashed on login when
  the library's recommended parameters change;
* a login for an unknown email still runs a full argon2 verify, so response
  time does not reveal which emails have accounts, and the error never says
  which of email or password was wrong;
* repeated failures for one email lock it for a while (in-process: the API
  runs as one uvicorn process).

Profiles created before accounts existed have no credentials. One can be
claimed exactly once, at signup, by naming its id: the browser that used it
still has the id in localStorage. A profile that already has credentials can
never be claimed.
"""

import hashlib
import re
import secrets
import threading
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from time import monotonic

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from data.models import AuthSession, User
from shared.exceptions import (
    AuthenticationError,
    DuplicateEmailError,
    InvalidInputError,
    LoginThrottledError,
    UserNotFoundError,
)

from ..core import BaseService

# Pragmatic, not RFC 5322: one @, a dot in the domain, no spaces. The address
# is never mailed, it is a login name, so this only has to catch typos.
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

_HASHER = PasswordHasher()
# Verified against when the email is unknown, so that path costs the same.
_DUMMY_HASH = _HASHER.hash("not-a-real-password-" + secrets.token_hex(8))

# Refresh the stored expiry at most this often: a touch per request would be a
# write per request.
_TOUCH_EVERY = timedelta(hours=1)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def normalise_email(email: str) -> str:
    return (email or "").strip().lower()


@dataclass
class SignedIn:
    """What signup and login hand back: the user and the raw cookie token."""

    user: User
    token: str
    expires_at: datetime


class _FailedLogins:
    """Failed attempts per email inside a sliding window. Shared per process."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._failures: dict[str, deque[float]] = defaultdict(deque)

    def locked(self, email: str, limit: int, window_s: float) -> bool:
        with self._lock:
            attempts = self._trim(email, window_s)
            return len(attempts) >= limit

    def record(self, email: str, window_s: float) -> None:
        with self._lock:
            self._trim(email, window_s).append(monotonic())

    def clear(self, email: str) -> None:
        with self._lock:
            self._failures.pop(email, None)

    def _trim(self, email: str, window_s: float) -> deque[float]:
        attempts = self._failures[email]
        cutoff = monotonic() - window_s
        while attempts and attempts[0] < cutoff:
            attempts.popleft()
        return attempts


FAILED_LOGINS = _FailedLogins()


class AuthService(BaseService):
    def __init__(self, db) -> None:
        super().__init__()
        self.users = db.users()
        self.sessions = db.auth_sessions()

    # --- signup / login ---------------------------------------------------------

    async def signup(
        self,
        email: str,
        password: str,
        display_name: str | None = None,
        claim_user_id: str | None = None,
    ) -> SignedIn:
        email = self._valid_email(email)
        self._valid_password(password)
        password_hash = _HASHER.hash(password)
        label = (display_name or "").strip()[:200]

        if await self.users.get_user_by_email(email) is not None:
            raise DuplicateEmailError("An account with this email already exists")

        user = await self._claimable(claim_user_id)

        if user is not None:
            await self.users.set_credentials(user.user_id, email, password_hash)
            if label and label != user.label:
                await self.users.rename(user.user_id, label)
            self.logger.info("Claimed legacy profile %r for a new account", user.user_id)
        else:
            user = User(user_id=str(uuid.uuid4()), label=label or email.split("@", 1)[0])
            await self.users.create_user(user)
            # Credentials go on in a second write: that is where the unique
            # email index turns a race between two signups into a 409.
            try:
                await self.users.set_credentials(user.user_id, email, password_hash)
            except DuplicateEmailError:
                await self.users.delete_user(user.user_id)
                raise
            self.logger.info("Created account %r", user.user_id)

        return await self._start_session(await self.users.get_user(user.user_id))

    async def login(self, email: str, password: str) -> SignedIn:
        email = normalise_email(email)
        window_s = self.settings.AUTH_LOGIN_LOCKOUT_MINUTES * 60

        if FAILED_LOGINS.locked(email, self.settings.AUTH_LOGIN_MAX_FAILURES, window_s):
            raise LoginThrottledError("Too many failed sign-in attempts. Try again later.")

        user = await self.users.get_user_by_email(email) if email else None

        if user is None or not user.password_hash:
            self._verify(_DUMMY_HASH, password)
            FAILED_LOGINS.record(email, window_s)
            raise AuthenticationError("Incorrect email or password")

        if not self._verify(user.password_hash, password):
            FAILED_LOGINS.record(email, window_s)
            self.logger.info("Failed sign-in for account %r", user.user_id)
            raise AuthenticationError("Incorrect email or password")

        FAILED_LOGINS.clear(email)

        if _HASHER.check_needs_rehash(user.password_hash):
            await self.users.set_password_hash(user.user_id, _HASHER.hash(password))

        return await self._start_session(user)

    # --- the cookie ----------------------------------------------------------------

    async def resolve(self, token: str | None) -> tuple[User, AuthSession] | None:
        """The signed-in user for a cookie token, or None."""
        if not token:
            return None

        token_hash = hash_token(token)
        session = await self.sessions.get_by_token_hash(token_hash)
        if session is None:
            return None

        now = datetime.now(timezone.utc)
        if _aware(session.expires_at) <= now:
            await self.sessions.delete(token_hash)
            return None

        try:
            user = await self.users.get_user(session.user_id)
        except UserNotFoundError:
            await self.sessions.delete(token_hash)
            return None

        if now - _aware(session.last_seen_at) >= _TOUCH_EVERY:
            expires = now + timedelta(days=self.settings.AUTH_SESSION_DAYS)
            await self.sessions.touch(token_hash, now, expires)
            session.last_seen_at, session.expires_at = now, expires

        return user, session

    async def logout(self, token: str | None) -> None:
        if token:
            await self.sessions.delete(hash_token(token))

    async def logout_everywhere(self, user_id: str, keep_token: str | None = None) -> int:
        return await self.sessions.delete_for_user(user_id, hash_token(keep_token) if keep_token else None)

    async def change_password(self, user: User, current: str, new: str, keep_token: str | None) -> None:
        if not user.password_hash or not self._verify(user.password_hash, current):
            raise AuthenticationError("The current password is incorrect")
        self._valid_password(new)
        await self.users.set_password_hash(user.user_id, _HASHER.hash(new))
        ended = await self.logout_everywhere(user.user_id, keep_token=keep_token)
        self.logger.info("Changed password for %r; ended %d other session(s)", user.user_id, ended)

    async def sweep_expired(self) -> int:
        return await self.sessions.delete_expired(datetime.now(timezone.utc))

    # --- helpers --------------------------------------------------------------------

    async def _start_session(self, user: User) -> SignedIn:
        token = secrets.token_urlsafe(32)
        now = datetime.now(timezone.utc)
        expires = now + timedelta(days=self.settings.AUTH_SESSION_DAYS)
        await self.sessions.create(
            AuthSession(token_hash=hash_token(token), user_id=user.user_id, expires_at=expires, last_seen_at=now)
        )
        return SignedIn(user=user, token=token, expires_at=expires)

    async def _claimable(self, claim_user_id: str | None) -> User | None:
        """A legacy profile this signup may take over, or None to create one.

        An id that does not exist, or that already belongs to an account, is
        ignored rather than reported: the answer must not tell a stranger
        whether a given profile id exists.
        """
        if not claim_user_id:
            return None
        try:
            user = await self.users.get_user(claim_user_id)
        except UserNotFoundError:
            return None
        return None if user.email or user.password_hash else user

    def _valid_email(self, email: str) -> str:
        email = normalise_email(email)
        if len(email) > 320 or not _EMAIL.match(email):
            raise InvalidInputError("Enter a valid email address")
        return email

    def _valid_password(self, password: str) -> None:
        minimum = self.settings.AUTH_PASSWORD_MIN_LENGTH
        if not password or len(password) < minimum:
            raise InvalidInputError(f"Use a password of at least {minimum} characters")
        if len(password) > 1024:
            raise InvalidInputError("That password is too long")

    @staticmethod
    def _verify(password_hash: str, password: str) -> bool:
        try:
            return _HASHER.verify(password_hash, password or "")
        except (VerifyMismatchError, VerificationError, InvalidHashError):
            return False


def _aware(value: datetime) -> datetime:
    """Mongo hands back naive UTC datetimes; Postgres aware ones."""
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
