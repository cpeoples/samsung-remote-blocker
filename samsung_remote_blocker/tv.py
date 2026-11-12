"""Samsung Tizen TV access: REST for app state, WebSocket for remote keys."""

from __future__ import annotations

from pathlib import Path

import requests
import urllib3
import websocket
from requests.adapters import HTTPAdapter
from samsungtvws import SamsungTVWS
from samsungtvws.exceptions import ConnectionFailure
from urllib3.util.retry import Retry

from . import NAME

# The TV serves a self-signed certificate; verification is disabled for TV requests only.
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

PORT = 8002
# How long the pairing prompt waits for Allow.
PAIR_TIMEOUT = 60.0
# Everything a TV call can raise: REST, WebSocket, socket and samsungtvws handshake errors.
TV_ERRORS = (requests.RequestException, websocket.WebSocketException, ConnectionFailure, OSError)


class TV:
    def __init__(self, ip: str, token_file: Path, timeout: float = 3.0):
        self.ip = ip
        self.timeout = timeout
        self.token_file = token_file
        self._apps_url = f"https://{ip}:{PORT}/api/v2/applications/"
        self._http = requests.Session()  # reused so polling doesn't pay a TLS handshake each time
        self._http.verify = False
        # The TV drops idle keep-alive connections without notice (~every 25 requests on a DU8000),
        # so retry idempotent calls once on a fresh connection. Launch is not retried.
        retry = Retry(total=1, connect=1, read=1, status=0, allowed_methods={"GET", "DELETE"})
        self._http.mount("https://", HTTPAdapter(max_retries=retry))
        self._remote = SamsungTVWS(host=ip, port=PORT, token_file=str(token_file), name=NAME, timeout=timeout)

    def app(self, app_id: str) -> dict | None:
        """App status ({id, name, running, visible, version}), or None if not installed."""
        r = self._http.get(self._apps_url + app_id, timeout=self.timeout)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json()

    def launch(self, app_id: str) -> None:
        self._http.post(self._apps_url + app_id, timeout=self.timeout).raise_for_status()

    def close(self, app_id: str) -> None:
        self._http.delete(self._apps_url + app_id, timeout=self.timeout).raise_for_status()

    def installed_apps(self) -> list[dict]:
        """Not answered by all firmware; may time out."""
        return self._remote.app_list() or []

    @property
    def paired(self) -> bool:
        return self.token_file.is_file() and bool(self.token_file.read_text().strip())

    def pair(self, timeout: float = PAIR_TIMEOUT) -> None:
        """Show the Allow prompt and wait. Raises UnauthorizedError if denied."""
        with SamsungTVWS(
            host=self.ip, port=PORT, token_file=str(self.token_file), name=NAME, timeout=timeout
        ) as remote:
            remote.open()

    def send_key(self, key: str) -> None:
        try:
            self._remote.send_key(key)
        except TV_ERRORS:
            # samsungtvws keeps a dead socket after a TV restart; drop it so the next call reconnects.
            self._remote.close()
            raise
