"""The watch loop: evict blocked apps, return to the last allowed app, resume playback."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Mapping

import requests
from samsungtvws.exceptions import UnauthorizedError

from .config import Settings
from .media import MediaServer
from .tv import PAIR_TIMEOUT, TV, TV_ERRORS

log = logging.getLogger(__name__)

# Closing the blocked app before the allowed app is in front drops the TV to Home.
CLOSE_DELAY = 1.0
# Consecutive failed polls before the TV is reported offline; single drops are routine.
OFFLINE_AFTER = 3
# Server checks while resuming, and the wait between them.
RESUME_TRIES = 6
RESUME_CHECK_SECONDS = 1.5
# Past this many seconds since leaving the media app, a blocked app was opened on purpose; don't resume.
LAUNCH_GRACE = 30.0
# An allowed app on screen this recently means an interrupt-only app replaced it; covers slow launches.
INTERRUPT_WINDOW = 10.0


class Watcher:
    def __init__(
        self,
        settings: Settings,
        tv: TV,
        servers: Mapping[str, MediaServer] | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.settings = settings
        self.tv = tv
        self.servers = dict(servers or {})  # app ID -> the server that knows what it's playing
        self.clock = clock
        self.sleep = sleep
        self.blocked = dict(settings.blocked)
        self.allowed = dict(settings.allowed)
        self.last_allowed: str | None = None
        self.last_playing: float | None = None
        self._media_left: float | None = None
        self._media_seen: float | None = None
        self._allowed_seen: float | None = None  # last poll with any allowed app on screen
        self._bounced: dict[str, float] = {}  # blocked app ID -> when it was last sent back
        self._let_through: set[str] = set()  # blocked apps left alone until an allowed app is back
        self._media_state: str | None = None
        self._position: tuple[str | None, int] | None = None  # (item ID, offset ms) last seen playing
        self._last_media_poll: float | None = None
        self._tv_failures = 0
        self._offline_servers: set[str] = set()
        self._pair_attempted = False

    def run(self) -> None:
        log.info(
            "Watching TV %s | blocking %s | allowed %s",
            self.tv.ip,
            ", ".join(sorted(set(self.blocked.values()))),
            ", ".join(sorted(set(self.allowed.values()))),
        )
        if not self.settings.resume:
            log.info("RESUME is off; will return to the last allowed app but not press Play")
        else:
            names = sorted({s.name for s in self.servers.values()})
            log.info("Resuming %s", " and ".join(names) if names else "nothing; no media server is set up")
        interrupt_only = sorted({self.blocked[a] for a in self.settings.interrupt_only if a in self.blocked})
        if interrupt_only:
            log.info("Blocking %s only when it opens over an allowed app", ", ".join(interrupt_only))
        if self.settings.override_seconds:
            log.info("A blocked app opened again within %g s is let through", self.settings.override_seconds)
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

    @property
    def server(self) -> MediaServer | None:
        return self.servers.get(self.last_allowed) if self.last_allowed else None

    def step(self) -> None:
        started = self.clock()
        statuses = {app_id: self.tv.app(app_id) for app_id in {**self.allowed, **self.blocked}}
        self._forget_uninstalled(statuses)

        for app_id, name in self.allowed.items():
            if (statuses.get(app_id) or {}).get("visible") and self.last_allowed != app_id:
                self.last_allowed = app_id
                self._position = None
                log.info("Now watching %s", name)

        if any((statuses.get(app_id) or {}).get("visible") for app_id in self.allowed):
            self._allowed_seen = started
            self._let_through.clear()

        # Time from when the media app was last seen; the TV can stall for seconds while an app launches.
        if self.server:
            if (statuses.get(self.last_allowed) or {}).get("visible"):
                self._media_left = None
                self._media_seen = started
                self._track_playback()
            elif self._media_left is None:
                self._media_left = self._media_seen if self._media_seen is not None else started

        for app_id, name in self.blocked.items():
            if (statuses.get(app_id) or {}).get("visible") and self._should_block(app_id, name, started):
                self._evict(app_id, name)
                self._bounced[app_id] = self.clock()

    def _should_block(self, app_id: str, name: str, now: float) -> bool:
        if app_id in self._let_through:
            return False
        # Only a reopen after the allowed app was back on screen counts, not a slow close of the first one.
        bounced = self._bounced.get(app_id)
        override = self.settings.override_seconds
        reopened = bounced is not None and self._allowed_seen is not None and self._allowed_seen > bounced
        if override and reopened and now - bounced <= override:
            log.info("%s opened again within %g s; letting it through", name, override)
            del self._bounced[app_id]
            self._let_through.add(app_id)
            return False
        interrupted = self._allowed_seen is not None and now - self._allowed_seen <= INTERRUPT_WINDOW
        if app_id in self.settings.interrupt_only and not interrupted:
            log.info("%s opened while no allowed app was on screen; letting it through", name)
            self._let_through.add(app_id)
            return False
        return True

    def _forget_uninstalled(self, statuses: dict[str, dict | None]) -> None:
        # Registry entries list every regional ID; drop the ones this TV lacks so they
        # stop costing a request per poll. Only trust 404s when some app answered,
        # since a TV that is still booting can 404 everything.
        if not any(statuses.values()):
            return
        for apps in (self.allowed, self.blocked):
            for app_id in [a for a in apps if a in statuses and statuses[a] is None]:
                log.info("%s (%s) is not installed; ignoring that ID", apps.pop(app_id), app_id)

    def _track_playback(self) -> None:
        now = self.clock()
        server = self.server
        if (
            self._last_media_poll is not None
            and now - self._last_media_poll < self.settings.plex_poll_seconds
        ):
            return
        self._last_media_poll = now
        try:
            session = server.session(self.tv.ip)
        except requests.RequestException as e:
            if server.name not in self._offline_servers:
                status = getattr(e.response, "status_code", None)
                reason = server.auth_hint if status == 401 else f"unreachable ({type(e).__name__})"
                log.warning("%s server %s; resume disabled until it's back", server.name, reason)
                self._offline_servers.add(server.name)
            return
        if server.name in self._offline_servers:
            log.info("%s server is reachable again", server.name)
            self._offline_servers.discard(server.name)
        state = session.state if session else None
        if state != self._media_state:
            log.debug("%s reports %s for %s", server.name, state or "no session", self.tv.ip)
            self._media_state = state
        if state in ("playing", "buffering"):
            self.last_playing = now
            if session.offset_ms or not self._position or self._position[0] != session.item_id:
                self._position = (session.item_id, session.offset_ms)

    def _evict(self, app_id: str, name: str) -> None:
        target = self.last_allowed
        now = self.clock()
        left = self._media_left if self._media_left is not None else now
        resume = (
            self.settings.resume
            and self.server is not None
            and self.last_playing is not None
            and left - self.last_playing <= self.settings.resume_window
            and now - left <= LAUNCH_GRACE
        )
        try:
            if target:
                log.info(
                    "%s launched -> back to %s%s", name, self.allowed[target], " (resuming)" if resume else ""
                )
                self.tv.launch(target)
            else:
                log.info("%s launched -> Home", name)
                self.tv.send_key("KEY_HOME")
        except TV_ERRORS as e:
            log.warning("Leaving %s failed (%s); sending Home", name, e)
            try:
                self.tv.send_key("KEY_HOME")
            except TV_ERRORS as e:
                log.error("Sending Home failed too (%s)", e)
            self.last_playing = None
            return
        # The blocked app is off screen now, so a failure past here must not send the viewer Home.
        try:
            if resume:
                self._resume()
            self.sleep(CLOSE_DELAY)
            log.debug("Closing %s", name)
            self.tv.close(app_id)
        except TV_ERRORS as e:
            log.warning("Cleanup after %s failed (%s)", name, e)
        self.last_playing = None
        self._position = None

    def _resume(self) -> None:
        """Press Play until the server says playing twice in a row; its first report can be stale."""
        server = self.server
        self.sleep(self.settings.resume_delay)
        self._restore_position(server)
        confirmed = 0
        for attempt in range(1, RESUME_TRIES + 1):
            if confirmed == 0:
                self.tv.send_key("KEY_PLAY")  # Play, not toggle, so a repeat can't pause it
            self.sleep(RESUME_CHECK_SECONDS)
            try:
                state = server.player_state(self.tv.ip)
            except requests.RequestException:
                return
            log.debug("Resume check %d/%d: %s reports %s", attempt, RESUME_TRIES, server.name, state)
            confirmed = confirmed + 1 if state in ("playing", "buffering") else 0
            if confirmed == 2:
                return
        log.warning("%s didn't resume; set RESUME_DELAY higher, or RESUME=false to turn it off", server.name)

    def _restore_position(self, server: MediaServer) -> None:
        # Stopping past the watched point clears Plex's resume point, so Play would start over.
        if not self._position or not self._position[0] or not self._position[1]:
            return
        item_id, offset_ms = self._position
        try:
            if server.restore_position(item_id, offset_ms):
                log.info("%s marked it watched; restored the position to %s", server.name, _clock(offset_ms))
        except requests.RequestException as e:
            log.warning("Couldn't restore the %s position (%s)", server.name, type(e).__name__)


def _clock(ms: int) -> str:
    minutes, seconds = divmod(ms // 1000, 60)
    return f"{minutes}:{seconds:02d}"
