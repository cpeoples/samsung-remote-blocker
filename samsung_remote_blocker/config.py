"""Settings, loaded from environment variables and validated once at startup."""

from __future__ import annotations

import ipaddress
import json
import logging
import os
import xml.etree.ElementTree as ET
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

DEFAULT_BLOCKED = "netflix,disney,prime,hulu,youtube"
DEFAULT_ALLOWED = "plex"


class ConfigError(Exception):
    pass


REGISTRY: dict[str, dict] = json.loads(Path(__file__).with_name("apps.json").read_text())
PLEX_APP_IDS = frozenset(REGISTRY["plex"]["ids"])


def parse_apps(spec: str, registry: Mapping[str, dict] = REGISTRY) -> dict[str, str]:
    """Turn "netflix,3201234:Foo" into {app_id: name}. Slugs expand to every known ID."""
    apps = {}
    for entry in filter(None, (e.strip() for e in spec.split(","))):
        slug = entry.lower()
        if slug in registry:
            apps.update(dict.fromkeys(registry[slug]["ids"], registry[slug]["name"]))
            continue
        app_id, _, name = (part.strip() for part in entry.partition(":"))
        if not app_id.isdigit():
            raise ConfigError(
                f"unknown app {entry!r}; use one of {', '.join(sorted(registry))} or an id:Name pair"
            )
        apps[app_id] = name or app_id
    return apps


@dataclass(frozen=True)
class Settings:
    tv_ip: str | None
    data_dir: Path
    blocked: dict[str, str]
    allowed: dict[str, str]
    poll_seconds: float
    plex_poll_seconds: float
    resume_window: float
    resume_delay: float
    resume: bool
    plex_url: str | None
    plex_token_file: Path
    plex_token: str | None = field(default=None, repr=False)  # keep it out of logs and tracebacks

    @property
    def tv_token_file(self) -> Path:
        # Per TV, so blockers for several TVs can share a volume.
        return self.data_dir / f"tv-token-{self.require_tv_ip()}.txt"

    def require_tv_ip(self) -> str:
        if not self.tv_ip:
            raise ConfigError("TV_IP is not set; find it with `samsung-remote-blocker discover`")
        return self.tv_ip


def _float(env: Mapping[str, str], name: str, default: float) -> float:
    raw = env.get(name) or default
    try:
        value = float(raw)
    except ValueError:
        raise ConfigError(f"{name} must be a number, got {raw!r}") from None
    if value <= 0:
        raise ConfigError(f"{name} must be positive, got {raw!r}")
    return value


def _bool(env: Mapping[str, str], name: str, default: bool) -> bool:
    raw = (env.get(name) or "").strip().lower()
    if not raw:
        return default
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    raise ConfigError(f"{name} must be true or false, got {raw!r}")


def _tv_ip(raw: str | None) -> str | None:
    if not raw:
        return None
    # Plex reports players by IP, so a hostname would break resume matching.
    try:
        return str(ipaddress.ip_address(raw.strip()))
    except ValueError:
        raise ConfigError(f"TV_IP must be an IP address, got {raw!r}") from None


def read_plex_token(env: Mapping[str, str], token_file: Path) -> str | None:
    """PLEX_TOKEN, else token_file, else PlexOnlineToken from PLEX_PREFERENCES."""
    if env.get("PLEX_TOKEN"):
        return env["PLEX_TOKEN"].strip()

    if token_file.is_file():
        token = token_file.read_text().strip()
        if token:
            return token

    prefs = env.get("PLEX_PREFERENCES")
    if prefs:
        try:
            return ET.parse(prefs).getroot().get("PlexOnlineToken") or None
        except (OSError, ET.ParseError) as e:
            # The token only enables resume, so don't stop blocking over it.
            log.warning("Can't read PlexOnlineToken from %s (%s); continuing without resume", prefs, e)
    return None


def load(env: Mapping[str, str] = os.environ) -> Settings:
    data_dir = Path(env.get("DATA_DIR") or ".")
    # One path for both reading and `plex-login` writing; PLEX_TOKEN_FILE can point at a secret.
    plex_token_file = Path(env.get("PLEX_TOKEN_FILE") or data_dir / ".plex-token")
    return Settings(
        tv_ip=_tv_ip(env.get("TV_IP")),
        data_dir=data_dir,
        blocked=parse_apps(env.get("BLOCKED_APPS") or DEFAULT_BLOCKED),
        allowed=parse_apps(env.get("ALLOWED_APPS") or DEFAULT_ALLOWED),
        poll_seconds=_float(env, "POLL_SECONDS", 0.25),
        plex_poll_seconds=_float(env, "PLEX_POLL_SECONDS", 1.0),
        resume_window=_float(env, "RESUME_WINDOW", 3.0),
        resume_delay=_float(env, "RESUME_DELAY", 2.0),
        resume=_bool(env, "RESUME", True),
        plex_url=(env.get("PLEX_URL") or "").rstrip("/") or None,
        plex_token_file=plex_token_file,
        plex_token=read_plex_token(env, plex_token_file),
    )
