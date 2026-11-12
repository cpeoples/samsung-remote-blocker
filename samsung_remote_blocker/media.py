"""What the watcher needs from a media server to resume playback."""

from __future__ import annotations

from typing import NamedTuple, Protocol


class Session(NamedTuple):
    state: str | None  # 'playing', 'paused' or 'buffering'
    item_id: str | None
    offset_ms: int


class MediaServer(Protocol):
    name: str
    auth_hint: str

    def session(self, player_ip: str) -> Session | None: ...

    def player_state(self, player_ip: str) -> str | None: ...

    def restore_position(self, item_id: str, offset_ms: int) -> bool: ...
