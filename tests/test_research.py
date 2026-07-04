"""Tests for the web research egress gate (pipeline/research.py, #186).

The security-critical guarantee — nothing reaches `_INCOMING/` without passing URL validation
and content sanitization — is enforced here, so these tests exercise the hygiene directly. The
network is never touched: `fetch` is unit-tested only for its pure helpers, and `deposit_*` take
an injected fake fetcher."""

import json
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from watchdog.pipeline import capture, research
from watchdog.pipeline.research import ResearchError


@pytest.fixture(autouse=True)
def _no_render(monkeypatch):
    """Keep these pre-#200 deposit tests network-free and deterministic: default every HTML deposit
    to the plain-fetch fallback. Rendered-capture behavior is exercised separately in
    tests/test_capture.py, which monkeypatches `capture.try_render` per test as needed."""
    monkeypatch.setattr(capture, "try_render", lambda *a, **k: None)


# ── URL validation (SSRF guard) ───────────────────────────────────────────────

@pytest.mark.parametrize("url", [
    "file:///etc/passwd",
    "javascript:alert(1)",
    "data:text/html,<h1>x</h1>",
    "ftp://example.com/x",
    "ssh://example.com",
    "https://",                 # no host
])
def test_validate_url_rejects_bad_schemes_and_missing_host(url):
    with pytest.raises(ResearchError):
        research.validate_url(url)


@pytest.mark.parametrize("url", [
    "http://localhost/admin",
    "http://127.0.0.1/secret",
    "https://10.0.0.5/internal",
    "http://192.168.1.1/router",
    "http://172.16.0.1/x",
    "http://169.254.169.254/latest/meta-data/",   # cloud metadata endpoint
    "http://[::1]/x",                              # IPv6 loopback
])
def test_validate_url_rejects_private_and_loopback_hosts(url):
    with pytest.raises(ResearchError):
        research.validate_url(url)


def test_validate_url_accepts_public(monkeypatch):
    # Pin DNS resolution to a public address so the test never hits the network.
    monkeypatch.setattr(research.socket, "getaddrinfo",
                        lambda *a, **k: [(2, 1, 6, "", ("93.184.216.34", 0))])
    assert research.validate_url("https://example.com/page") == "https://example.com/page"


def test_check_host_public_rejects_private_resolution(monkeypatch):
    # A public-looking name that resolves into RFC1918 (DNS rebinding-style) is still rejected.
    monkeypatch.setattr(research.socket, "getaddrinfo",
                        lambda *a, **k: [(2, 1, 6, "", ("10.1.2.3", 0))])
    with pytest.raises(ResearchError):
        research.validate_url("https://sneaky.example.com/")


# ── Content type → extension ──────────────────────────────────────────────────

def test_extension_for_strips_charset_params():
    assert research.extension_for("text/html; charset=utf-8", "http://e.com/a") == ".html"


def test_extension_for_falls_back_to_url_suffix():
    assert research.extension_for("application/octet-stream", "http://e.com/report.pdf") == ".pdf"


def test_extension_for_rejects_unsupported():
    with pytest.raises(ResearchError):
        research.extension_for("application/zip", "http://e.com/archive")


# ── TLS context (#243) ────────────────────────────────────────────────────────

def test_ssl_context_relaxes_strict_verification_only():
    import ssl
    ctx = research._ssl_context()
    assert not (ctx.verify_flags & ssl.VERIFY_X509_STRICT)  # tolerate non-conformant proxy CAs
    assert ctx.verify_mode == ssl.CERT_REQUIRED             # chain validation still on
    assert ctx.check_hostname                               # hostname checking still on


def test_fetch_and_wayback_openers_use_relaxed_context():
    import ssl
    import urllib.request
    for opener in (research._opener, research._wayback_opener):
        https = [h for h in opener.handlers if isinstance(h, urllib.request.HTTPSHandler)]
        assert any(h._context is not None
                   and not (h._context.verify_flags & ssl.VERIFY_X509_STRICT) for h in https)


# ── Sanitization ──────────────────────────────────────────────────────────────

def test_sanitize_html_strips_script_and_iframe():
    out = research.sanitize_html("a<script>steal()</script>b<iframe src=evil></iframe>c")
    assert "<script" not in out.lower() and "<iframe" not in out.lower()
    assert "steal()" not in out


def test_sanitize_html_strips_unclosed_tags():
    out = research.sanitize_html("ok<script src=x.js>trailing")
    assert "<script" not in out.lower()


def test_sanitize_html_strips_event_handler_attributes():
    out = research.sanitize_html('<a href="http://e.com" onclick="steal()">link</a>')
    assert "onclick" not in out.lower()


def test_sanitize_html_preserves_structure():
    out = research.sanitize_html(
        "<h1>Title</h1><table><tr><td>cell</td></tr></table>"
        '<a href="http://e.com/x">a link</a>')
    assert "<table" in out and "<td" in out
    assert "<a href=\"http://e.com/x\"" in out


def test_neutralize_defangs_wikilinks_and_collapses_newlines():
    out = research.neutralize("Forged [[entities/person/x]] link\n---\ninjected")
    assert "[[" not in out and "]]" not in out
    assert "\n" not in out


# ── Sidecar ───────────────────────────────────────────────────────────────────

def test_build_sidecar_is_parseable_and_carries_provenance():
    text = research.build_sidecar(source="https://e.com/x", title="A Filing",
                                  source_type="official-registry",
                                  relevance="names the director", obtained="2026-06-30")
    data = yaml.safe_load(text)
    assert data["source"] == "https://e.com/x"
    assert data["obtained"] == "2026-06-30"
    assert data["retrieved_by"] == "research-mode"
    assert data["source_type"] == "official-registry"


def test_build_sidecar_defangs_malicious_title():
    text = research.build_sidecar(source="https://e.com/x", title="Pwn [[entities/x]]",
                                  source_type="blog", relevance="", obtained="2026-06-30")
    data = yaml.safe_load(text)
    assert "[[" not in data["title"]


# ── Deposit ───────────────────────────────────────────────────────────────────

def _fake_fetcher(body: bytes, content_type: str, final_url: str | None = None):
    def _fetch(url, **kwargs):
        return body, content_type, final_url or url
    return _fetch


def test_deposit_one_writes_document_and_sidecar(tmp_path):
    vault = tmp_path / "vault"
    fetcher = _fake_fetcher(b"<html><body><script>x</script>hello</body></html>", "text/html")
    path = research.deposit_one(vault, "https://e.com/post", title="A Post",
                                source_type="news", relevance="why", obtained="2026-06-30",
                                fetcher=fetcher)
    assert path.exists()
    assert path.suffix == ".html"
    assert path.parent == vault / "_INCOMING"
    # Script was stripped from the deposited body.
    assert b"script" not in path.read_bytes()
    # Sidecar sits beside it under the `<name>.html.yml` convention the ingest path reads.
    sidecar = path.with_name(path.name + ".yml")
    assert sidecar.exists()
    data = yaml.safe_load(sidecar.read_text())
    assert data["source"] == "https://e.com/post"
    assert data["retrieved_by"] == "research-mode"


def test_deposit_one_records_retrieved_by(tmp_path):
    # `watchdog fetch` deposits carry an honest acquisition tag, not "research-mode" (#197).
    vault = tmp_path / "vault"
    path = research.deposit_one(vault, "https://e.com/x",
                                fetcher=_fake_fetcher(b"<html>ok</html>", "text/html"),
                                retrieved_by="fetch")
    data = yaml.safe_load((path.with_name(path.name + ".yml")).read_text())
    assert data["retrieved_by"] == "fetch"
    assert data["source_type"] == "unverified"   # no reliability tag supplied for a bare URL


def test_deposit_one_size_cap_enforced(tmp_path):
    # fetch (the real one) enforces the cap; deposit_one passes max_bytes through. Here we assert
    # the cap travels to the fetcher.
    seen = {}

    def _fetch(url, **kwargs):
        seen["max_bytes"] = kwargs["max_bytes"]
        return b"<html>x</html>", "text/html", url

    research.deposit_one(tmp_path / "v", "https://e.com/x", max_bytes=1234, fetcher=_fetch)
    assert seen["max_bytes"] == 1234


def test_deposit_name_is_stable_per_url(tmp_path):
    vault = tmp_path / "v"
    f = _fake_fetcher(b"<html>x</html>", "text/html")
    p1 = research.deposit_one(vault, "https://e.com/x", title="T", fetcher=f)
    p2 = research.deposit_one(vault, "https://e.com/x", title="T", fetcher=f)
    # Re-pulling the same URL overwrites the same file (idempotent recovery), never duplicates.
    assert p1 == p2
    assert len(list((vault / "_INCOMING").glob("*.html"))) == 1


def test_parse_worklist_skips_comments_and_blanks():
    entries = research.parse_worklist(
        "https://a.com\tTitle A\tnews\twhy a\n# a comment\n\nhttps://b.com\n")
    assert entries == [
        {"url": "https://a.com", "title": "Title A", "source_type": "news", "relevance": "why a"},
        {"url": "https://b.com", "title": "", "source_type": "", "relevance": ""},
    ]


def test_deposit_many_continues_past_failures(tmp_path):
    vault = tmp_path / "v"
    calls = {"n": 0}

    def _fetch(url, **kwargs):
        calls["n"] += 1
        if "bad" in url:
            raise ResearchError("refused: simulated")
        return b"<html>ok</html>", "text/html", url

    entries = [
        {"url": "https://good1.com"},
        {"url": "https://bad.com"},
        {"url": "https://good2.com"},
    ]
    results = research.deposit_many(vault, entries, fetcher=_fetch)
    assert [bool(r.path) for r in results] == [True, False, True]
    assert results[1].error and "simulated" in results[1].error
    assert len(list((vault / "_INCOMING").glob("*.html"))) == 2


# ── Durable worklist store (#196) ──────────────────────────────────────────────

def test_pending_count_and_queue_path(tmp_path):
    vault = tmp_path / "v"
    assert research.pending_count(vault) == 0  # no worklist yet
    q = research.queue_path(vault)
    q.parent.mkdir(parents=True)
    q.write_text("https://a.com\tA\tnews\twhy\n# comment\n\nhttps://b.com\n", encoding="utf-8")
    assert research.pending_count(vault) == 2  # comments/blanks skipped


def test_read_queue_text_falls_back_to_old_tmp_location(tmp_path):
    vault = tmp_path / "v"
    old = vault / research._OLD_QUEUE_REL
    old.parent.mkdir(parents=True)
    old.write_text("https://legacy.com\n", encoding="utf-8")
    assert research.pending_count(vault) == 1  # read via fallback
    # A worklist at the new path takes precedence over the old one.
    new = research.queue_path(vault)
    new.parent.mkdir(parents=True)
    new.write_text("https://a.com\nhttps://b.com\n", encoding="utf-8")
    assert research.pending_count(vault) == 2


def test_serialize_worklist_round_trips(tmp_path):
    text = "https://a.com\tTitle A\tnews\twhy a\nhttps://b.com\n"
    entries = research.parse_worklist(text)
    assert research.parse_worklist(research.serialize_worklist(entries)) == entries


def test_retain_pending_keeps_only_given_rows(tmp_path):
    vault = tmp_path / "v"
    q = research.queue_path(vault)
    q.parent.mkdir(parents=True)
    q.write_text("https://a.com\tA\nhttps://b.com\tB\n", encoding="utf-8")
    entries = research.parse_worklist(q.read_text())
    research.retain_pending(vault, [entries[1]])  # keep only the second row
    remaining = research.parse_worklist(q.read_text())
    assert [e["url"] for e in remaining] == ["https://b.com"]


def test_retain_pending_empty_deletes_worklist(tmp_path):
    vault = tmp_path / "v"
    q = research.queue_path(vault)
    q.parent.mkdir(parents=True)
    q.write_text("https://a.com\n", encoding="utf-8")
    old = vault / research._OLD_QUEUE_REL
    old.parent.mkdir(parents=True)
    old.write_text("https://a.com\n", encoding="utf-8")
    research.retain_pending(vault, [])
    assert not q.exists()
    assert not old.exists()  # the fallback is cleared too, so it can't re-nag


# ── seen_urls: re-fetch avoidance (#196, Part A) ───────────────────────────────

def test_seen_urls_unions_documents_and_incoming_sidecars(tmp_path):
    vault = tmp_path / "v"
    reg = vault / ".watchdog" / "Registry"
    reg.mkdir(parents=True)
    (reg / "documents.json").write_text(json.dumps({
        "sha1": {"source": "https://doc.example/ingested"},
        "sha2": {"source": None},        # a non-web document — no source
        "sha3": {},                      # missing source key
    }))
    incoming = vault / "_INCOMING"
    incoming.mkdir()
    (incoming / "a.html.yml").write_text("source: https://inflight.example/downloaded\ntitle: t\n")
    (incoming / "b.pdf.yml").write_text("title: no-source-here\n")
    assert research.seen_urls(vault) == {
        "https://doc.example/ingested",
        "https://inflight.example/downloaded",
    }


def test_seen_urls_empty_when_no_artifacts(tmp_path):
    assert research.seen_urls(tmp_path / "v") == set()


# ── Size cap (#196) ────────────────────────────────────────────────────────────

def test_default_size_cap_is_20_mib():
    assert research._MAX_BYTES_DEFAULT == 20 * 1024 * 1024


def test_fetch_rejects_over_cap_with_readable_error(monkeypatch):
    cap = 2 * 1024 * 1024  # 2 MiB

    class _Headers:
        def get_content_type(self):
            return "text/html"

    class _Resp:
        def read(self, n):
            return b"x" * n            # always fills the read → looks over-cap
        headers = _Headers()
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False

    class _Opener:
        def open(self, req, timeout=None):
            return _Resp()

    monkeypatch.setattr(research, "_check_host_public", lambda host: None)
    monkeypatch.setattr(research, "_opener", _Opener())
    with pytest.raises(ResearchError) as exc:
        research.fetch("https://example.com/big", max_bytes=cap)
    assert "2 MiB download cap" in str(exc.value)


# ── Wayback Machine — Save Page Now (#201) ─────────────────────────────────────

class _WaybackResp:
    def __init__(self, payload: bytes):
        self._payload = payload

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_save_to_wayback_returns_snapshot_url_on_accepted_job():
    seen = {}

    def opener(req, timeout=None):
        seen["method"] = req.get_method()
        seen["auth"] = req.headers.get("Authorization")
        return _WaybackResp(b'{"url":"https://e.com/x","job_id":"spn2-abc"}')

    url = research.save_to_wayback("https://e.com/x", "acc", "sec", opener=opener)
    assert url == "https://web.archive.org/web/https://e.com/x"
    assert seen["method"] == "POST"          # a save must be a POST, not a GET
    assert seen["auth"] == "LOW acc:sec"     # SPN2 auth header


def test_save_to_wayback_returns_none_when_no_job_accepted():
    def opener(req, timeout=None):
        return _WaybackResp(b'{"message":"Rate limit"}')  # no job_id
    assert research.save_to_wayback("https://e.com/x", "acc", "sec", opener=opener) is None


def test_save_to_wayback_swallows_network_errors():
    def opener(req, timeout=None):
        raise OSError("network down")
    # Best-effort: archiving must never raise into the deposit path.
    assert research.save_to_wayback("https://e.com/x", "acc", "sec", opener=opener) is None


def test_deposit_one_stamps_archived_when_wayback_enabled(tmp_path, monkeypatch):
    vault = tmp_path / "v"
    monkeypatch.setattr(research, "save_to_wayback",
                        lambda url, a, s, **kw: "https://web.archive.org/web/" + url)
    path = research.deposit_one(vault, "https://e.com/x", title="T",
                                fetcher=_fake_fetcher(b"<html>ok</html>", "text/html"),
                                wayback=("acc", "sec"))
    sidecar = yaml.safe_load((path.parent / f"{path.name}.yml").read_text())
    assert sidecar["archived"] == "https://web.archive.org/web/https://e.com/x"


def test_deposit_one_no_archived_field_without_wayback(tmp_path):
    vault = tmp_path / "v"
    path = research.deposit_one(vault, "https://e.com/x", title="T",
                                fetcher=_fake_fetcher(b"<html>ok</html>", "text/html"))
    sidecar = yaml.safe_load((path.parent / f"{path.name}.yml").read_text())
    assert "archived" not in sidecar


def test_deposit_one_survives_failed_archiving(tmp_path, monkeypatch):
    vault = tmp_path / "v"
    monkeypatch.setattr(research, "save_to_wayback", lambda url, a, s, **kw: None)  # archiving failed
    path = research.deposit_one(vault, "https://e.com/x", title="T",
                                fetcher=_fake_fetcher(b"<html>ok</html>", "text/html"),
                                wayback=("acc", "sec"))
    # The deposit still lands; the sidecar just carries no archived URL.
    assert path.exists()
    sidecar = yaml.safe_load((path.parent / f"{path.name}.yml").read_text())
    assert "archived" not in sidecar
