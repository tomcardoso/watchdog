"""scripts/merge_mac_feed.py: the release workflow's merge of the per-architecture Mac update feeds."""

import importlib.util
from pathlib import Path

import pytest
import yaml


def _module():
    path = Path(__file__).resolve().parents[1] / "scripts" / "merge_mac_feed.py"
    spec = importlib.util.spec_from_file_location("merge_mac_feed", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


merge_mac_feed = _module()

# The latest-mac.yml 1.0.5 shipped with, from a build that made both architectures in one job.
PUBLISHED = """\
version: 1.0.5
files:
  - url: Watchdog-1.0.5-x64.zip
    sha512: LdVRgUKMuifQ4UAg1qZcZxhcGgIH3h8k8Nsb7IXe0C8RxWSVPjjmbCd+3vn5r6r+7VFL5z16rDiAskSKIoKTpA==
    size: 163687081
  - url: Watchdog-1.0.5-arm64.zip
    sha512: W96/QyprPPRqrt8/+mmw/7wZyDzmjsy19UYV1YT0WxnaDnKt74LnW0fTntgqChymCI+eZrewBJFkLegqWEfVrw==
    size: 154355398
  - url: Watchdog-1.0.5-x64.dmg
    sha512: f+D4vo0oWpuTcE1SPmWQOa9g6a8JiS4lEp7eovCFsETmvFuICBQ0znJRxPHHJmvcMD1ZBZknNQNoqWrvS8xv9g==
    size: 164559071
  - url: Watchdog-1.0.5-arm64.dmg
    sha512: fsxsSK3mS5RUVRBSJKOG0DXmiM6/sonF8uYFpAlqTcqS4o0j9o9E7SwBf/0AGGdkYQbNl0vlGkVYjxspD1CKxw==
    size: 155298190
path: Watchdog-1.0.5-x64.zip
sha512: LdVRgUKMuifQ4UAg1qZcZxhcGgIH3h8k8Nsb7IXe0C8RxWSVPjjmbCd+3vn5r6r+7VFL5z16rDiAskSKIoKTpA==
releaseDate: '2026-10-19T21:38:42.238Z'
"""


def _single(published, arch, release_date):
    """The feed a one-architecture job writes: its own zip and dmg, its zip as `path`."""
    files = [f for f in published["files"] if ("arm64" in f["url"]) == (arch == "arm64")]
    return {
        "version": published["version"],
        "files": files,
        "path": files[0]["url"],
        "sha512": files[0]["sha512"],
        "releaseDate": release_date,
    }


def _split():
    published = yaml.safe_load(PUBLISHED)
    arm = _single(published, "arm64", "2026-10-19T21:34:50.000Z")
    intel = _single(published, "x64", published["releaseDate"])
    return published, arm, intel


@pytest.mark.parametrize("order", ["arm-first", "intel-first"])
def test_merge_reproduces_a_two_architecture_build(order):
    published, arm, intel = _split()
    feeds = [arm, intel] if order == "arm-first" else [intel, arm]
    assert merge_mac_feed.merge(feeds) == published


def test_main_writes_a_feed_that_reads_back_the_same(tmp_path):
    published, arm, intel = _split()
    paths = []
    for name, feed in (("arm64", arm), ("x64", intel)):
        p = tmp_path / f"{name}.yml"
        p.write_text(yaml.safe_dump(feed, sort_keys=False))
        paths.append(str(p))
    out = tmp_path / "latest-mac.yml"
    merge_mac_feed.main([str(out), *paths])
    text = out.read_text()
    assert yaml.safe_load(text) == published
    # The date stays a string, as electron-builder writes it, not a YAML timestamp.
    assert "releaseDate: '2026-10-19T21:38:42.238Z'" in text


def test_a_missing_architecture_fails():
    _, arm, _ = _split()
    with pytest.raises(ValueError, match="feed per architecture"):
        merge_mac_feed.merge([arm])
    with pytest.raises(ValueError, match="one arm64 and one x64 zip"):
        merge_mac_feed.merge([arm, dict(arm, files=[])])


def test_mismatched_versions_fail():
    _, arm, intel = _split()
    with pytest.raises(ValueError, match="disagree on the version"):
        merge_mac_feed.merge([arm, dict(intel, version="1.0.6")])


def test_the_same_file_twice_fails():
    _, arm, _ = _split()
    with pytest.raises(ValueError, match="more than one feed"):
        merge_mac_feed.merge([arm, arm])
