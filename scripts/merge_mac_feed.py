"""Merge the per-architecture `latest-mac.yml` files into the one installed Macs read.

The release workflow builds the Apple silicon and Intel apps in separate jobs, so the two
notarizations run at once. Each job's electron-builder writes a `latest-mac.yml` listing only its
own architecture's zip and dmg; installed copies read a single file and pick the arm64 or x64 entry
from it by name. This writes that file, in the shape a two-architecture build produces: Intel's
files first, zips before dmgs, and the top-level `path`/`sha512` naming the Intel zip.

    python scripts/merge_mac_feed.py OUT FEED [FEED ...]
"""

import sys

import yaml


def _order(entry):
    url = entry["url"]
    return (url.endswith(".dmg"), "arm64" in url)


def merge(feeds):
    """One feed from several single-architecture feeds; raises ValueError on anything off."""
    if len(feeds) < 2:
        raise ValueError(f"expected a feed per architecture, got {len(feeds)}")
    versions = {str(f.get("version")) for f in feeds}
    if len(versions) != 1:
        raise ValueError(f"feeds disagree on the version: {sorted(versions)}")

    files = [entry for f in feeds for entry in f.get("files") or []]
    urls = [entry["url"] for entry in files]
    duplicates = sorted({u for u in urls if urls.count(u) > 1})
    if duplicates:
        raise ValueError(f"a file is in more than one feed: {duplicates}")
    zips = [u for u in urls if u.endswith(".zip")]
    arm = [u for u in zips if "arm64" in u]
    intel = [u for u in zips if "arm64" not in u]
    if len(arm) != 1 or len(intel) != 1:
        raise ValueError(f"expected one arm64 and one x64 zip, got {zips}")

    files.sort(key=_order)
    merged = dict(feeds[0])
    merged["files"] = files
    merged["path"] = files[0]["url"]
    merged["sha512"] = files[0]["sha512"]
    merged["releaseDate"] = max(str(f["releaseDate"]) for f in feeds)
    return merged


def main(argv):
    if len(argv) < 3:
        sys.exit(__doc__)
    out, paths = argv[0], argv[1:]
    feeds = []
    for path in paths:
        with open(path, encoding="utf-8") as fh:
            feeds.append(yaml.safe_load(fh))
    merged = merge(feeds)
    with open(out, "w", encoding="utf-8") as fh:
        yaml.safe_dump(merged, fh, sort_keys=False, width=1000)
    print(f"{out}: {', '.join(e['url'] for e in merged['files'])}")


if __name__ == "__main__":
    main(sys.argv[1:])
