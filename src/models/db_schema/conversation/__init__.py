"""Documents backing the chat feature: users, sessions, chats, messages.

Grouped in one package because they are one feature and always change together
— a field added to Chat is usually a field the Message writer cares about too.

Identity here is deliberately thin: a User is an opaque uuid with no password,
no email and no verification. "New user" mints one, "current user" is whatever
id the browser kept. It exists to scope conversations, not to prove anything.
"""

from .chat import Chat
from .message import Message
from .session import Session
from .user import User

__all__ = ["Chat", "Message", "Session", "User"]
