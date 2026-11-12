"""Jellyfin server session lookup."""

from __future__ import annotations

import requests

from .media import Session

TICKS_PER_MS = 10_000


class Jellyfin:
    name = "Jellyfin"
    auth_hint = "rejected JELLYFIN_API_KEY"

    def __init__(self, url: str, api_key: str, timeout: float = 2.0):
        self.url = url
        self.timeout = timeout
        self._http = requests.Session()
        self._http.headers.update(
            {"Accept": "application/json", "Authorization": f'MediaBrowser Token="{api_key}"'}
        )

    def player_state(self, player_ip: str) -> str | None:
        session = self.session(player_ip)
        return session.state if session else None

    def session(self, player_ip: str) -> Session | None:
        r = self._http.get(f"{self.url}/Sessions", timeout=self.timeout)
        r.raise_for_status()
        for s in r.json():
            endpoint = s.get("RemoteEndPoint") or ""
            item = s.get("NowPlayingItem")
            if item and (endpoint == player_ip or endpoint.startswith(player_ip + ":")):
                play = s.get("PlayState") or {}
                state = "paused" if play.get("IsPaused") else "playing"
                return Session(state, item.get("Id"), (play.get("PositionTicks") or 0) // TICKS_PER_MS)
        return None

    def restore_position(self, item_id: str, offset_ms: int) -> bool:
        """Jellyfin on the TV pauses in the background instead of stopping, so nothing is lost."""
        return False
