from importlib.metadata import version, PackageNotFoundError

try:
    __version__ = version("media-api")
except PackageNotFoundError:
    __version__ = "unknown"
