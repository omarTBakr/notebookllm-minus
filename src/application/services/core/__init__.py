"""The pieces every other service leans on.

`BaseService` is settings and a logger named after the subclass's own
module, which is why a log line says which service wrote it without anyone
passing a name around. `IdempotencyService` answers "is this already
running?" and is used by every route that queues work — it belongs to no
feature in particular, which is exactly why it is here.
"""

from .BaseService import BaseService
from .IdempotencyService import IdempotencyService

__all__ = ["BaseService", "IdempotencyService"]
