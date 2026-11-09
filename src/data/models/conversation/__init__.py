"""Documents backing the chat feature: users, sessions, chats, messages.

Grouped in one package because they are one feature and always change together
— a field added to Chat is usually a field the Message writer cares about too.

A User is an account: an email and an argon2 password hash, plus the opaque
uuid every other record hangs off. Profiles made before accounts existed have
no credentials until someone claims them at signup. AuthSession is one
signed-in browser (a hashed cookie token), unrelated to Session, which groups
notebooks.
"""

from .auth_session import AuthSession
from .chat import Chat
from .message import Message
from .personal_user_info import PersonalUserInfo
from .session import Session
from .user import User

__all__ = ["AuthSession", "Chat", "Message", "PersonalUserInfo", "Session", "User"]
