from importlib.metadata import version, PackageNotFoundError

try:
    __version__ = version("pipedream-fiction")
except PackageNotFoundError:
    __version__ = "unknown"
