"""Start as root only to fix /data ownership, then run as the user that owns Plex's files."""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from pathlib import Path

from .config import ConfigError

log = logging.getLogger(__name__)

DEFAULT_ID = 1000


def target_ids(env: Mapping[str, str]) -> tuple[int, int]:
    """PUID/PGID, else the owner of PLEX_PREFERENCES so it stays readable, else 1000."""
    if env.get("PUID"):
        try:
            uid = int(env["PUID"])
            return uid, int(env.get("PGID") or uid)
        except ValueError:
            raise ConfigError("PUID and PGID must be numbers") from None
    prefs = env.get("PLEX_PREFERENCES")
    if prefs:
        try:
            st = os.stat(prefs)
        except OSError:
            pass
        else:
            if st.st_uid != 0:
                return st.st_uid, st.st_gid
    return DEFAULT_ID, DEFAULT_ID


def drop_root(env: Mapping[str, str], data_dir: Path) -> None:
    if os.getuid() != 0:
        return  # started with `user:`; nothing to change
    uid, gid = target_ids(env)
    if uid == 0:
        raise ConfigError("PUID=0 would run as root; use the uid that owns your Plex files")
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        for path in (data_dir, *data_dir.iterdir()):
            os.chown(path, uid, gid)
    except OSError as e:
        log.warning("Can't set ownership of %s (%s)", data_dir, e)
    os.setgroups([])
    os.setgid(gid)
    os.setuid(uid)
    log.info("Running as %d:%d", uid, gid)
