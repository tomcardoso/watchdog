"""Tests for rendered web capture (pipeline/capture.py, #200).

Three layers: pure helpers (`_allow_request`, `rewrite_css`) are unit-tested directly; the
`deposit_one` integration is exercised with `capture.try_render` monkeypatched (mirroring the
fake-fetcher pattern in test_research.py) so no real render happens; and one end-to-end test drives
an actual headless-Chromium render against a local `http.server`, skipped cleanly when Playwright or
Chromium isn't available."""

import base64
import http.server
import socketserver
import sys
import threading
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from watchdog.pipeline import capture, research


# ── _allow_request: per-request SSRF guard ────────────────────────────────────

def test_allow_request_accepts_https_with_passing_host_check():
    cache = {}
    assert capture._allow_request("https://example.com/a", cache, host_check=lambda h: None) is True


@pytest.mark.parametrize("url", ["ftp://example.com/x", "data:text/html,<h1>x</h1>"])
def test_allow_request_rejects_non_http_schemes(url):
    cache = {}
    assert capture._allow_request(url, cache, host_check=lambda h: None) is False


def test_allow_request_rejects_when_host_check_raises():
    def _raise(host):
        raise ValueError("refused")
    assert capture._allow_request("https://example.com/a", {}, host_check=_raise) is False


def test_allow_request_caches_verdict_per_host():
    calls = []

    def _check(host):
        calls.append(host)

    cache = {}
    capture._allow_request("https://example.com/a", cache, host_check=_check)
    capture._allow_request("https://example.com/b", cache, host_check=_check)
    assert calls == ["example.com"]  # second call for the same host hit the cache


# ── rewrite_css ────────────────────────────────────────────────────────────────

def test_rewrite_css_resolves_relative_ref_against_css_url():
    assets = {"http://e.com/img/img.png": "data:image/png;base64,AAA="}
    out = capture.rewrite_css("body { background: url(img.png); }",
                              "http://e.com/img/style.css", assets)
    assert "url(data:image/png;base64,AAA=)" in out


def test_rewrite_css_handles_quoted_forms():
    assets = {"http://e.com/img.png": "data:image/png;base64,AAA="}
    out = capture.rewrite_css('body { background: url("img.png"); }',
                              "http://e.com/style.css", assets)
    assert "data:image/png;base64,AAA=" in out
    out2 = capture.rewrite_css("body { background: url('img.png'); }",
                               "http://e.com/style.css", assets)
    assert "data:image/png;base64,AAA=" in out2


def test_rewrite_css_handles_import_statement():
    assets = {"http://e.com/x.css": "data:text/css;base64,AAA="}
    out = capture.rewrite_css('@import "x.css";', "http://e.com/style.css", assets)
    assert "data:text/css;base64,AAA=" in out


def test_rewrite_css_neuters_uncaptured_ref():
    out = capture.rewrite_css("body { background: url(missing.png); }",
                              "http://e.com/style.css", {})
    assert "url(data:,)" in out


def test_rewrite_css_leaves_existing_data_uri_untouched():
    css = "body { background: url(data:image/png;base64,AAA=); }"
    assert capture.rewrite_css(css, "http://e.com/style.css", {}) == css


# ── deposit_one integration (capture.try_render monkeypatched) ────────────────

def _fake_fetcher(body: bytes, content_type: str):
    def _fetch(url, **kwargs):
        return body, content_type, url
    return _fetch


def test_deposit_one_uses_rendered_bytes_when_capture_succeeds(tmp_path, monkeypatch):
    rendered = b"<!DOCTYPE html>\n<html><body>RENDERED SNAPSHOT</body></html>"
    monkeypatch.setattr(capture, "try_render", lambda *a, **k: rendered)
    vault = tmp_path / "vault"
    path = research.deposit_one(
        vault, "https://e.com/post", title="A Post",
        fetcher=_fake_fetcher(b"<html><script>x</script>original</html>", "text/html"))
    assert path.read_bytes() == rendered
    sidecar = yaml.safe_load((path.parent / f"{path.name}.yml").read_text())
    assert sidecar["capture"] == "rendered"


def test_deposit_one_falls_back_to_sanitized_fetch_when_capture_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(capture, "try_render", lambda *a, **k: None)
    vault = tmp_path / "vault"
    fetched = b"<html><script>steal()</script>hello</html>"
    path = research.deposit_one(
        vault, "https://e.com/post", title="A Post", fetcher=_fake_fetcher(fetched, "text/html"))
    body = path.read_text()
    assert "<script" not in body.lower()
    assert "hello" in body
    sidecar = yaml.safe_load((path.parent / f"{path.name}.yml").read_text())
    assert sidecar["capture"] == "plain"


def test_deposit_one_non_html_has_no_capture_field(tmp_path, monkeypatch):
    def _should_not_be_called(*a, **k):
        raise AssertionError("try_render must not run for a non-HTML deposit")
    monkeypatch.setattr(capture, "try_render", _should_not_be_called)
    vault = tmp_path / "vault"
    path = research.deposit_one(
        vault, "https://e.com/report.pdf", title="Report",
        fetcher=_fake_fetcher(b"%PDF-1.4 fake", "application/pdf"))
    sidecar = yaml.safe_load((path.parent / f"{path.name}.yml").read_text())
    assert "capture" not in sidecar


# ── render_available + the install tip ────────────────────────────────────────

def test_render_available_false_when_chromium_binary_missing(monkeypatch, tmp_path):
    pytest.importorskip("playwright")
    # Playwright imports fine, but pointing the browsers path at an empty dir makes the
    # Chromium executable unresolvable — the two-step install's likelier missing half.
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(tmp_path / "empty"))
    assert capture.render_available() is False


def test_report_deposits_tips_install_when_html_saved_without_renderer(monkeypatch, capsys, tmp_path):
    from watchdog.cmd import research as cmd_research
    monkeypatch.setattr(cmd_research.capture, "render_available", lambda: False)
    results = [research.Deposit("http://e.com/a", tmp_path / "a-12345678.html")]
    cmd_research._report_deposits(results, wayback=None, requeued_failures=False)
    out = capsys.readouterr().out
    assert "playwright install chromium" in out


def test_report_deposits_no_tip_when_renderer_available_or_no_html(monkeypatch, capsys, tmp_path):
    from watchdog.cmd import research as cmd_research
    monkeypatch.setattr(cmd_research.capture, "render_available", lambda: True)
    results = [research.Deposit("http://e.com/a", tmp_path / "a-12345678.html")]
    cmd_research._report_deposits(results, wayback=None, requeued_failures=False)
    assert "playwright" not in capsys.readouterr().out
    monkeypatch.setattr(cmd_research.capture, "render_available", lambda: False)
    results = [research.Deposit("http://e.com/r.pdf", tmp_path / "r-12345678.pdf")]
    cmd_research._report_deposits(results, wayback=None, requeued_failures=False)
    assert "playwright" not in capsys.readouterr().out


# ── End-to-end render (real Chromium) ──────────────────────────────────────────

class _ThreadingHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True


_PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=")

# The blocked <img> points at 127.0.0.2 — the same loopback server (macOS/Linux route the whole
# 127/8 block locally), but a host the test's host_check refuses. If the route guard works, the
# request is aborted in-browser and the server never sees a hit for /blocked.png.
_SPA_HTML_TEMPLATE = """<!doctype html>
<html><head><link rel="stylesheet" href="/style.css"></head>
<body>
<div id="root"></div>
<img src="/dot.png">
<img src="http://127.0.0.2:{port}/blocked.png">
<script>document.getElementById('root').textContent = 'MARKER_TEXT_XYZ';</script>
</body></html>"""

_SPA_CSS = b"body { background: url(dot.png); }"


class _SPAHandler(http.server.BaseHTTPRequestHandler):
    requested: list  # class attribute set per-test: every path the server actually served

    def do_GET(self):
        self.requested.append(self.path)
        if self.path == "/":
            port = self.server.server_address[1]
            body, ctype = _SPA_HTML_TEMPLATE.format(port=port).encode(), "text/html"
        elif self.path == "/style.css":
            body, ctype = _SPA_CSS, "text/css"
        elif self.path in ("/dot.png", "/blocked.png"):
            body, ctype = _PNG_1X1, "image/png"
        else:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass  # keep test output quiet


def test_render_capture_end_to_end_spa():
    pytest.importorskip("playwright")
    from playwright.sync_api import Error as PlaywrightError

    def _host_check(host):
        if host != "127.0.0.1":
            raise research.ResearchError(f"refused: {host}")  # 127.0.0.2 must be aborted in-browser

    _SPAHandler.requested = []
    server = _ThreadingHTTPServer(("127.0.0.1", 0), _SPAHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        try:
            result = capture.render_capture(
                f"http://127.0.0.1:{port}/", max_bytes=10 * 1024 * 1024,
                host_check=_host_check)
        except PlaywrightError as e:
            # Skip only when the browser binary genuinely isn't installed — any other
            # Playwright error is a real failure and must not masquerade as a skip.
            if "executable doesn't exist" in str(e).lower():
                pytest.skip("Chromium is not installed in this environment")
            raise
    finally:
        server.shutdown()
        thread.join(timeout=5)

    out = result.decode("utf-8")
    assert "MARKER_TEXT_XYZ" in out          # the inline <script> ran before we stripped it
    assert "<script" not in out.lower()      # and is gone from the saved snapshot
    assert 'src="data:image/png' in out      # <img> inlined as a data URI
    assert "<style" in out and "data:image/png" in out  # stylesheet inlined with its asset resolved
    assert "Content-Security-Policy" in out
    # The SSRF guard aborted the disallowed-host request inside the browser: the server never
    # served /blocked.png, and the snapshot carries no reference to the blocked origin.
    assert "/blocked.png" not in _SPAHandler.requested
    assert "127.0.0.2" not in out
