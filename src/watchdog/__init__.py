try:
    from importlib.metadata import version
    __version__ = version("watchdog-intel")
except Exception:
    __version__ = "unknown"
