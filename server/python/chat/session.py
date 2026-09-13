"""Conversation memory behind a protocol.

The API is stateless and a Lambda container is disposable, so the in-memory
maps in strideChatService.ts cannot simply move. The store here is the same
policy (last 12 messages, 30-minute TTL) behind an interface small enough
that a DynamoDB binding in phase 3A is one class. Only the text of each
turn is kept: the tool_use and tool_result blocks of past turns are not
replayed, which keeps the prefix short and stable for the cache and is what
the TypeScript chat did too.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Protocol, Tuple

from .config import SESSION_MAX_MESSAGES, SESSION_TTL_SECONDS


class SessionStore(Protocol):
    def history(self, session_id: Optional[str]) -> List[Dict[str, Any]]: ...
    def append(self, session_id: Optional[str], user_text: str, assistant_text: str) -> None: ...
    def clear(self, session_id: Optional[str]) -> None: ...


@dataclass
class InMemorySessionStore:
    ttl_seconds: int = SESSION_TTL_SECONDS
    max_messages: int = SESSION_MAX_MESSAGES
    _data: Dict[str, Tuple[float, List[Dict[str, Any]]]] = field(default_factory=dict, repr=False)

    def history(self, session_id: Optional[str]) -> List[Dict[str, Any]]:
        if not session_id:
            return []
        hit = self._data.get(session_id)
        if not hit:
            return []
        touched, messages = hit
        if time.time() - touched > self.ttl_seconds:
            del self._data[session_id]
            return []
        return [dict(m) for m in messages]

    def append(self, session_id: Optional[str], user_text: str, assistant_text: str) -> None:
        if not session_id:
            return
        messages = self.history(session_id)
        messages.append({"role": "user", "content": user_text})
        messages.append({"role": "assistant", "content": assistant_text})
        # Keep whole turns: an odd trim would leave a leading assistant message.
        while len(messages) > self.max_messages:
            messages = messages[2:]
        self._data[session_id] = (time.time(), messages)

    def clear(self, session_id: Optional[str]) -> None:
        if session_id:
            self._data.pop(session_id, None)


class NullSessionStore:
    """No memory. Every turn stands alone."""

    def history(self, session_id: Optional[str]) -> List[Dict[str, Any]]:
        return []

    def append(self, session_id: Optional[str], user_text: str, assistant_text: str) -> None:
        return None

    def clear(self, session_id: Optional[str]) -> None:
        return None
