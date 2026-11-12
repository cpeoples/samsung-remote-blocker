"""Command-line entry point: `samsung-remote-blocker <command>`."""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import sys
from pathlib import Path

from samsungtvws.exceptions import UnauthorizedError

from . import NAME, __version__, discover, plex, privileges
from .config import REGISTRY, ConfigError, Settings, load
from .plex import Plex
from .tv import PAIR_TIMEOUT, TV, TV_ERRORS
from .watcher import Watcher


def tv_for(settings: Settings) -> TV:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    if not os.access(settings.data_dir, os.W_OK):
        raise ConfigError(
            f"{settings.data_dir} isn't writable by uid {os.getuid()}, so the TV token can't be saved; "
            f"chown it to {os.getuid()}:{os.getgid()}"
        )
    token_file = settings.tv_token_file
    legacy = settings.data_dir / "tv-token.txt"  # 0.1.0 kept one token for any TV
    if legacy.is_file() and not token_file.exists():
        legacy.rename(token_file)
    return TV(settings.require_tv_ip(), token_file)


def scan_hosts(cidr: str) -> set[str]:
    try:
        return discover.subnet_hosts(cidr)
    except ValueError as e:
        raise argparse.ArgumentTypeError(str(e)) from None


def cmd_watch(settings: Settings, args: argparse.Namespace) -> None:
    client = None
    if settings.resume and settings.plex_url and settings.plex_token:
        client = Plex(settings.plex_url, settings.plex_token)
    Watcher(settings, tv_for(settings), client).run()


def cmd_pair(settings: Settings, args: argparse.Namespace) -> None:
    tv = tv_for(settings)
    print(f"Press Allow on the TV (waiting {PAIR_TIMEOUT:.0f} s)...")
    tv.pair()
    print(f"Paired; token saved to {settings.tv_token_file}")


def cmd_discover(settings: Settings, args: argparse.Namespace) -> None:
    tvs = discover.discover(args.scan)
    if args.json:
        print(json.dumps(tvs, indent=2))
        return
    if not tvs:
        hint = "" if args.scan else " SSDP may be blocked here; try --scan 192.168.0.0/24"
        sys.exit("No Samsung TVs found." + hint)
    print(f"{'IP':<16} {'NAME':<28} {'MODEL':<16} {'MAC':<18} POWER")
    for t in tvs:
        print(
            f"{t['ip']:<16} {(t['name'] or '?')[:27]:<28} {t['model'] or '?':<16} "
            f"{t['mac'] or '?':<18} {t['power'] or '?'}"
        )


def cmd_app(settings: Settings, args: argparse.Namespace) -> None:
    tv = tv_for(settings)
    for target in args.apps:
        ids = REGISTRY[target]["ids"] if target in REGISTRY else [target]
        for app_id in ids:
            print(app_id, tv.app(app_id) or "not installed")


def cmd_apps(settings: Settings, args: argparse.Namespace) -> None:
    for app in tv_for(settings).installed_apps():
        print(app.get("appId"), app.get("name"))


def cmd_key(settings: Settings, args: argparse.Namespace) -> None:
    tv_for(settings).send_key(args.key)


def cmd_plex_login(settings: Settings, args: argparse.Namespace) -> None:
    plex.link_device(settings.plex_token_file)


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog=NAME, description="Kick blocked apps off a Samsung TV.")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    p.add_argument("-v", "--verbose", action="store_true", help="debug logging, same as LOG_LEVEL=debug")
    sub = p.add_subparsers(dest="command")

    sub.add_parser("watch", help="run the watcher (default)").set_defaults(func=cmd_watch)
    sub.add_parser("pair", help="pair with the TV").set_defaults(func=cmd_pair)

    d = sub.add_parser("discover", help="find Samsung TVs on the network")
    d.add_argument(
        "--scan", metavar="CIDR", type=scan_hosts, help="probe every host in a subnet instead of SSDP"
    )
    d.add_argument("--json", action="store_true")
    d.set_defaults(func=cmd_discover)

    a = sub.add_parser("app", help="show status of apps by ID or registry slug")
    a.add_argument("apps", nargs="+", metavar="ID_OR_SLUG")
    a.set_defaults(func=cmd_app)

    sub.add_parser("apps", help="list installed apps (not all firmware answers)").set_defaults(func=cmd_apps)

    k = sub.add_parser("key", help="send one remote key, e.g. KEY_PLAY")
    k.add_argument("key")
    k.set_defaults(func=cmd_key)

    sub.add_parser("plex-login", help="get a Plex token via plex.tv/link").set_defaults(func=cmd_plex_login)
    return p


def main(argv: list[str] | None = None) -> None:
    args = parser().parse_args(argv)
    # Tokens are the only files written, by us and by samsungtvws; keep them owner-only.
    os.umask(0o077)
    debug = args.verbose or os.environ.get("LOG_LEVEL", "").lower() == "debug"
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    # Debug is for our logger only; samsungtvws logs the pairing token.
    logging.getLogger("samsung_remote_blocker").setLevel(logging.DEBUG if debug else logging.INFO)
    logging.getLogger("urllib3.connectionpool").setLevel(logging.ERROR)
    logging.getLogger("samsungtvws").setLevel(logging.WARNING)
    # As PID 1 in a container, Python ignores SIGTERM, so `docker stop` would end in SIGKILL.
    signal.signal(signal.SIGTERM, signal.default_int_handler)
    try:
        privileges.drop_root(os.environ, Path(os.environ.get("DATA_DIR") or "."))
        (getattr(args, "func", None) or cmd_watch)(load(), args)
    except ConfigError as e:
        sys.exit(f"Config error: {e}")
    except UnauthorizedError:
        sys.exit(
            "The TV refused the connection. Accept the Allow prompt, or remove the denied "
            "device under Settings > General > External Device Manager, then try again."
        )
    except TV_ERRORS as e:
        # The watcher handles its own outages; this is for one-off commands.
        sys.exit(f"Error: {e}")
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
