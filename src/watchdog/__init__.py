import json
import subprocess

try:
    from importlib.metadata import version, Distribution
    __version__ = version("watchdog-intel")

    # Detect editable (local dev) install and append git short hash
    try:
        direct_url = Distribution.from_name("watchdog-intel").read_text("direct_url.json")
        if direct_url:
            info = json.loads(direct_url)
            if info.get("dir_info", {}).get("editable", False):
                pkg_dir = info.get("url", "").removeprefix("file://")
                if pkg_dir:
                    r = subprocess.run(
                        ["git", "rev-parse", "--short", "HEAD"],
                        cwd=pkg_dir,
                        capture_output=True,
                        text=True,
                        timeout=3,
                    )
                    if r.returncode == 0:
                        __version__ += f"-dev+{r.stdout.strip()}"
    except Exception:
        pass
except Exception:
    __version__ = "unknown"
