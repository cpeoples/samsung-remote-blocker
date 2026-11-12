NAME = "samsung-remote-blocker"  # CLI name, and how the TV and Plex list this client

try:
    from importlib.metadata import PackageNotFoundError, version

    __version__ = version(NAME)
except PackageNotFoundError:  # not installed
    __version__ = "unknown"
