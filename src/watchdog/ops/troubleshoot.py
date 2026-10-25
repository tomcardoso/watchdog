"""A copy of an investigation for troubleshooting (D323): one zip of what a bug report needs,
written to a folder the reporter chose, with no model call.

`plan(vault, originals)` decides what goes in, and is the one place that list lives: the app's
size preview (`vault.troubleshootingSize`) and the operation both read it. Everything comes from
inside the investigation folder: symbolic links are skipped and every path is checked to resolve
inside it, so nothing from `~/.watchdog` (credentials, chats, logs, the telemetry store) can reach
the zip. The originals and their text (`morgue/`, `incoming/`, the queue and staging folders, the
OCR'd text positions) go in only when asked for.
"""

from __future__ import annotations

import datetime
import json
import os
import platform
import re
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path

from watchdog.ops import op, require_vault

# Always included: Watchdog's records of the investigation and the notes rendered from them.
ALWAYS_DIRS = (
    ".watchdog/registry",      # entities, documents, merges, resolutions, verification, processing log, usage
    ".watchdog/extracted",     # each document's stored extraction
    ".watchdog/timeline",      # raw and canonical events
    ".watchdog/briefings",     # each briefing's citation map and status line
    "entities",
    "documents",
    "briefings",
)
ALWAYS_FILES = (
    ".watchdog/processing-state.json",
    "context.md", "log.md", "timeline.md", "index.md", "verification.md", "merges.md", "requests.md",
)
# Only with `originals`: the documents themselves and the text read from them.
ORIGINALS_DIRS = (
    "morgue",
    "incoming",
    ".watchdog/queue",
    ".watchdog/staging",
    ".watchdog/text-positions",
)
# What the manifest says was left out on purpose, for whoever opens the copy.
LEFT_OUT = (
    "credentials, chats and logs (they are kept outside the investigation folder)",
    "version history (.watchdog/history/) and backups (.watchdog/backups/)",
    "saved answers and threads (queries/, wiki/)",
    "search indexes (.embeddings/, .fulltext/) and Claude setup (.claude/)",
)
MANIFEST_NAME = "manifest.json"
FORMAT_VERSION = 1


@dataclass(frozen=True)
class Entry:
    path: Path        # on disk
    arcname: str      # inside the investigation, with forward slashes
    size: int
    original: bool    # part of the opt-in originals


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root)
        return True
    except (OSError, ValueError):
        return False


def _walk(vault: Path, root: Path, rel: str, original: bool):
    base = vault / rel
    if base.is_symlink() or not base.is_dir():
        return
    for dirpath, dirnames, filenames in os.walk(base, followlinks=False):
        d = Path(dirpath)
        # Hidden folders and files below a root are locks, scratch or the system's own (.DS_Store).
        dirnames[:] = sorted(n for n in dirnames if not n.startswith(".") and not (d / n).is_symlink())
        for name in sorted(filenames):
            if name.startswith("."):
                continue
            p = d / name
            if p.is_symlink() or not p.is_file() or not _inside(p, root):
                continue
            try:
                size = p.stat().st_size
            except OSError:
                continue
            yield Entry(p, p.relative_to(vault).as_posix(), size, original)


def plan(vault: Path, originals: bool = False) -> list[Entry]:
    """Every file the copy holds, in a stable order."""
    vault = Path(vault)
    root = vault.resolve()
    out: list[Entry] = []
    for rel in ALWAYS_FILES:
        p = vault / rel
        if p.is_file() and not p.is_symlink() and _inside(p, root):
            out.append(Entry(p, rel, p.stat().st_size, False))
    for rel in ALWAYS_DIRS:
        out.extend(_walk(vault, root, rel, False))
    if originals:
        for rel in ORIGINALS_DIRS:
            out.extend(_walk(vault, root, rel, True))
    return out


def sizes(vault: Path) -> dict:
    """What the copy would hold, without and with the originals, before compression."""
    entries = plan(vault, originals=True)
    base = [e for e in entries if not e.original]
    orig = [e for e in entries if e.original]
    return {"files": len(base), "bytes": sum(e.size for e in base),
            "originals_files": len(orig), "originals_bytes": sum(e.size for e in orig)}


def _app_settings() -> dict:
    """The reporter's own settings from `config.json`: the known ones only, never a secret or a
    folder path."""
    from watchdog import config
    from watchdog.settings import SETTINGS
    cfg = config.read()
    return {k: v for k, v in sorted(cfg.items())
            if k in SETTINGS and not SETTINGS[k].get("secret") and SETTINGS[k].get("type") != "path"}


def _investigation_settings(vault: Path) -> dict:
    """`.watchdog/settings.json`: which labelled key each provider bills (D290), never a key."""
    p = vault / ".watchdog" / "settings.json"
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    keys = data.get("keys") if isinstance(data, dict) else None
    if not isinstance(keys, dict):
        return {}
    return {"keys": {prov: {"label": k.get("label")} for prov, k in keys.items() if isinstance(k, dict)}}


def manifest(vault: Path, entries: list[Entry], originals: bool) -> dict:
    import watchdog
    return {
        "format_version": FORMAT_VERSION,
        "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "watchdog_version": watchdog.__version__,
        "platform": {"system": platform.system(), "release": platform.release(),
                     "machine": platform.machine(), "python": platform.python_version()},
        "includes_originals": originals,
        "files": len(entries),
        "bytes": sum(e.size for e in entries),
        "settings": _app_settings(),
        "investigation_settings": _investigation_settings(vault),
        "left_out": list(LEFT_OUT),
    }


def _name(vault: Path) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", vault.name.lower()).strip("-") or "investigation"
    stamp = datetime.datetime.now().strftime("%Y-%m-%d-%H%M%S")
    return f"{slug}-troubleshooting-{stamp}"


def _fmt_size(n: int) -> str:
    for unit in ("bytes", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n} {unit}" if unit == "bytes" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n} bytes"


@op("troubleshooting-copy")
def troubleshooting_copy(rep, vault: Path, *, output: str, originals: bool = False) -> dict:
    """Write one zip of the investigation's records and notes (and, with `originals`, its
    documents) into the folder `output`, for sending with a bug report."""
    require_vault(vault)
    out_dir = Path(output).expanduser()
    if not out_dir.is_dir():
        sys.exit(f"Error: the folder {out_dir} does not exist.")
    name = _name(vault)
    target = out_dir / f"{name}.zip"
    n = 2
    while target.exists():
        target = out_dir / f"{name}-{n}.zip"
        n += 1
    part = target.with_name(target.name + ".part")
    # Listed before the zip exists, so a folder chosen inside the investigation never copies it.
    entries = plan(vault, originals)
    rep.progress("stage", stage="troubleshooting-copy", done=0, total=len(entries))
    top = target.stem
    try:
        with zipfile.ZipFile(part, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as z:
            z.writestr(f"{top}/{MANIFEST_NAME}",
                       json.dumps(manifest(vault, entries, originals), indent=2, ensure_ascii=False))
            for i, e in enumerate(entries, 1):
                try:
                    z.write(e.path, f"{top}/{e.arcname}")
                except OSError as err:
                    rep.warn(f"Left out {e.arcname}: {err.strerror or err}.")
                if i % 200 == 0:
                    rep.progress("stage", stage="troubleshooting-copy", done=i, total=len(entries))
        os.replace(part, target)
    except BaseException:
        part.unlink(missing_ok=True)
        raise
    size = target.stat().st_size
    rep.log(f"Wrote {target.name} ({_fmt_size(size)}, {len(entries)} files) to {out_dir}.")
    if originals:
        rep.log("It includes the original documents and the text read from them.")
    else:
        rep.log("The original documents are not included, but the notes and extractions quote them.")
    return {"output": str(target), "bytes": size, "files": len(entries), "originals": originals}
