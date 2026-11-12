"""The watch loop: evict blocked apps, return to the last allowed app, resume Plex."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable

import requests
from samsungtvws.exceptions import UnauthorizedError

from .config import PLEX_APP_IDS, Settings
from .plex import Plex
from .tv import PAIR_TIMEOUT, TV, TV_ERRORS

log = logging.getLogger(__name__)

# Closing the blocked app before the allowed app is in front drops the TV to Home.
CLOSE_DELAY = 1.0
# Consecutive failed polls before the TV is reported offline; single drops are routine.
OFFLINE_AFTER = 3


class Watcher:
    def __init__(
        self,
        settings: Settings,
        tv: TV,
        plex: Plex | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.settings = settings
        self.tv = tv
        self.plex = plex
        self.clock = clock
        self.sleep = sleep
        self.blocked = dict(settings.blocked)
        self.allowed = dict(settings.allowed)
        self.last_allowed: str | None = None
        self.last_playing: float | None = None
        self._last_plex_poll: float | None = None
        self._tv_failures = 0
        self._plex_online = True
        self._pair_attempted = False

    def run(self) -> None:
        log.info(
            "Watching TV %s | blocking %s | allowed %s",
            self.tv.ip,
            ", ".join(sorted(set(self.blocked.values()))),
            ", ".join(sorted(set(self.allowed.values()))),
        )
        if not self.plex:
            log.info("PLEX_URL or Plex token not set; will return to Plex but not resume")
        while True:
            try:
                self.step()
                if self._tv_failures >= OFFLINE_AFTER:
                    log.info("TV is reachable again")
                    self._pair_attempted = False
                self._tv_failures = 0
                self.ensure_paired()
            except requests.RequestException as e:
                self._tv_failures += 1
                if self._tv_failures == OFFLINE_AFTER:
                    log.warning("TV unreachable (%s); retrying", type(e).__name__)
            self.sleep(self.settings.poll_seconds)

    def ensure_paired(self) -> None:
        """Only keys need pairing, so a missed prompt is retried when the TV next comes on."""
        if self._pair_attempted or self.tv.paired:
            return
        self._pair_attempted = True
        log.info("Not paired yet; press Allow on the TV (waiting %.0f s)", PAIR_TIMEOUT)
        try:
            self.tv.pair()
        except UnauthorizedError:
            log.error(
                "The TV denied pairing. Remove the device under Settings > General > "
                "External Device Manager, then restart the container"
            )
            return
        except TV_ERRORS as e:
            if isinstance(e, PermissionError):
                log.error("Paired, but the token can't be saved (%s); fix the /data permissions", e)
            else:
                log.warning("No answer to the Allow prompt (%s); will ask again when the TV next comes on", e)
            return
        log.info("Paired with the TV")

    def step(self) -> None:
        statuses = {app_id: self.tv.app(app_id) for app_id in {**self.allowed, **self.blocked}}
        self._forget_uninstalled(statuses)

        for app_id, name in self.allowed.items():
            if (statuses.get(app_id) or {}).get("visible") and self.last_allowed != app_id:
                self.last_allowed = app_id
                log.info("Now watching %s", name)

        self._track_plex()

        for app_id, name in self.blocked.items():
            if (statuses.get(app_id) or {}).get("visible"):
                self._evict(app_id, name)

    def _forget_uninstalled(self, statuses: dict[str, dict | None]) -> None:
        # Registry entries list every regional ID; drop the ones this TV lacks so they
        # stop costing a request per poll. Only trust 404s when some app answered,
        # since a TV that is still booting can 404 everything.
        if not any(statuses.values()):
            return
        for apps in (self.allowed, self.blocked):
            for app_id in [a for a in apps if a in statuses and statuses[a] is None]:
                log.info("%s (%s) is not installed; ignoring that ID", apps.pop(app_id), app_id)

    def _track_plex(self) -> None:
        now = self.clock()
        if not (self.plex and self.last_allowed in PLEX_APP_IDS):
            return
        if self._last_plex_poll is not None and now - self._last_plex_poll < self.settings.plex_poll_seconds:
            return
        self._last_plex_poll = now
        try:
            state = self.plex.player_state(self.tv.ip)
        except requests.RequestException as e:
            if self._plex_online:
                status = getattr(e.response, "status_code", None)
                reason = (
                    "rejected the token; run plex-login again"
                    if status == 401
                    else f"unreachable ({type(e).__name__})"
                )
                log.warning("Plex server %s; resume disabled until it's back", reason)
                self._plex_online = False
            return
        if not self._plex_online:
            log.info("Plex server is reachable again")
            self._plex_online = True
        if state in ("playing", "buffering"):
            self.last_playing = now

    def _evict(self, app_id: str, name: str) -> None:
        target = self.last_allowed
        resume = (
            target in PLEX_APP_IDS
            and self.last_playing is not None
            and self.clock() - self.last_playing <= self.settings.resume_window
        )
        try:
            if target:
                log.info(
                    "%s launched -> back to %s%s", name, self.allowed[target], " (resuming)" if resume else ""
                )
                self.tv.launch(target)
                if resume:
                    self.sleep(self.settings.resume_delay)
                    self.tv.send_key("KEY_PLAY")  # Play, not toggle, so it can't pause by mistake
            else:
                log.info("%s launched -> Home", name)
                self.tv.send_key("KEY_HOME")
            self.sleep(CLOSE_DELAY)
            self.tv.close(app_id)
        except TV_ERRORS as e:
            log.warning("Recovery from %s failed (%s); sending Home", name, e)
            try:
                self.tv.send_key("KEY_HOME")
            except TV_ERRORS as e:
                log.error("Sending Home failed too (%s)", e)
        self.last_playing = None
