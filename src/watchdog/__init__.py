import functools


@functools.lru_cache(maxsize=1)
def _version() -> str:
    """The installed version; an editable (development) install appends the git short hash.
    Computed on first use rather than at import, since the hash costs a `git` subprocess."""
    import json
    import subprocess
    try:
        from importlib.metadata import Distribution, version
        v = version("watchdog-intel")
    except Exception:
        return "unknown"
    try:
        direct_url = Distribution.from_name("watchdog-intel").read_text("direct_url.json")
        info = json.loads(direct_url) if direct_url else {}
        if info.get("dir_info", {}).get("editable", False):
            pkg_dir = info.get("url", "").removeprefix("file://")
            if pkg_dir:
                r = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=pkg_dir,
                                   capture_output=True, text=True, timeout=3)
                if r.returncode == 0:
                    v += f"-dev+{r.stdout.strip()}"
    except Exception:
        pass
    return v


def __getattr__(name: str):
    if name == "__version__":
        return _version()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
