"""Plex Media Server session lookup and plex.tv device-link login."""

from __future__ import annotations

import time
import uuid
from pathlib import Path

import requests

from . import NAME

PLEX_TV = "https://plex.tv/api/v2"


class Plex:
    def __init__(self, url: str, token: str, timeout: float = 2.0):
        self.url = url
        self.timeout = timeout
        self._http = requests.Session()
        # Header rather than query string, so the token never appears in URLs or exception text.
        self._http.headers.update({"Accept": "application/json", "X-Plex-Token": token})

    def player_state(self, player_ip: str) -> str | None:
        """'playing', 'paused' or 'buffering' for the session on player_ip, else None."""
        r = self._http.get(f"{self.url}/status/sessions", timeout=self.timeout)
        r.raise_for_status()
        for session in r.json().get("MediaContainer", {}).get("Metadata", []):
            player = session.get("Player", {})
            if player.get("address") == player_ip:
                return player.get("state")
        return None


def link_device(token_file: Path, poll_seconds: float = 2.0) -> None:
    """Get a token via https://plex.tv/link without handling the user's password.
    The PIN expires after about 15 minutes."""
    headers = {
        "Accept": "application/json",
        "X-Plex-Product": NAME,
        "X-Plex-Client-Identifier": str(uuid.uuid4()),
    }
    r = requests.post(f"{PLEX_TV}/pins", headers=headers, data={"strong": "false"}, timeout=10)
    r.raise_for_status()
    pin = r.json()
    print(f"Go to https://plex.tv/link and enter: {pin['code']}")
    print("Waiting for approval (Ctrl-C to cancel)...")

    while True:
        time.sleep(poll_seconds)
        r = requests.get(f"{PLEX_TV}/pins/{pin['id']}", headers=headers, timeout=10)
        if r.status_code == 404:
            raise SystemExit("PIN expired; run plex-login again.")
        r.raise_for_status()
        token = r.json().get("authToken")
        if token:
            break

    token_file.parent.mkdir(parents=True, exist_ok=True)
    token_file.write_text(token + "\n")
    print(f"Token saved to {token_file}")
