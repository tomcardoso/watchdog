"""Rendered web capture (#200) — headless-Chromium snapshotting for `pipeline/research.py`.

`research.deposit_one` fetches every URL once with urllib for type-detection and provenance (the
existing, SSRF-guarded plain fetch); when the deposit is HTML, this module gives it a second,
richer capture: render the page in headless Chromium, inline every reachable asset (images, fonts,
stylesheets) as a data URI, strip all script/JS-execution surface, and save one self-contained
`.html` file. A single-tag strip of the *fetched markup* never captured what an SPA actually
renders on screen, or a static page's CSS and images — Chromium's own engine is the only faithful
way to get either.

The SSRF guard from `research.py` is re-applied to **every subresource request** the rendered page
makes (route interception, not just the top-level URL), so rendering a page can't be turned into a
probe of internal network space via an `<img>`, stylesheet, or `fetch()` call the page itself
issues.

Playwright is an optional dependency (`pip install watchdog-intel[web]`, plus a one-time `playwright
install chromium`): `render_available()` and `try_render()` let `research.py` degrade to the plain,
nh3-sanitized fetch when Playwright isn't installed, the Chromium binary is missing, or a render
fails for any reason — a broken or absent browser never blocks a deposit, it only yields a
less-faithful capture."""

from __future__ import annotations

import base64
import re
from pathlib import Path
from urllib.parse import urljoin, urlsplit

from watchdog.pipeline import research


class CaptureError(Exception):
    """Rendering failed, or the captured snapshot exceeds the deposit's size cap."""


_MAX_ASSET_BYTES = 5 * 1024 * 1024  # 5 MiB — per-asset inlining cap, independent of the deposit-wide max_bytes


def render_available() -> bool:
    """True iff Playwright's sync API is importable **and** its Chromium binary is installed —
    i.e. both halves of the optional two-step install are done. Backs the post-download install
    tip in `cmd/research.py`; forgetting `playwright install chromium` is the likelier miss, so
    checking the import alone would silence the tip exactly when it's needed. The capture path
    itself never consults this — it just attempts a render and falls back on any failure."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return False
    try:
        with sync_playwright() as p:
            return Path(p.chromium.executable_path).exists()
    except Exception:
        return False


# ── Per-request SSRF guard (route interception) ───────────────────────────────

def _allow_request(url: str, cache: dict[str, bool], host_check=research._check_host_public) -> bool:
    """Pure decision function backing the Playwright route handler: every subresource request a
    rendered page makes — XHR, `<img>`, CSS, fonts — is re-checked against the same public-host rule
    as the top-level fetch, not just the URL the journalist queued. `data:`/`blob:`/`ws:` and other
    non-http(s) schemes are refused outright. Verdicts are cached per host so a page with dozens of
    same-origin requests doesn't re-resolve DNS for each one."""
    parts = urlsplit(url)
    if parts.scheme.lower() not in ("http", "https"):
        return False
    host = parts.hostname
    if not host:
        return False
    if host not in cache:
        try:
            host_check(host)
            cache[host] = True
        except Exception:
            cache[host] = False
    return cache[host]


# ── CSS asset rewriting ────────────────────────────────────────────────────────

_CSS_URL_RE = re.compile(r"""url\(\s*(?P<q>['"]?)(?P<ref>[^'")]*)(?P=q)\s*\)""", re.IGNORECASE)
_CSS_IMPORT_STR_RE = re.compile(r"""@import\s+(?P<q>['"])(?P<ref>[^'"]*)(?P=q)""", re.IGNORECASE)


def rewrite_css(css_text: str, css_url: str, assets: dict[str, str]) -> str:
    """Rewrite every `url(...)` and `@import` reference in a stylesheet fetched from `css_url`:
    resolve relative refs against it (`urljoin`), substitute the data URI from `assets` when the
    resource was captured, and neuter anything uncaptured to `url(data:,)` so a saved snapshot can
    never phone home for a background image or `@font-face` source. Pre-existing `data:` URIs are
    left untouched."""

    def _resolve(ref: str) -> str | None:
        return assets.get(urljoin(css_url, ref.strip()))

    def _sub_url(m: re.Match) -> str:
        ref = m.group("ref")
        if ref.strip().startswith("data:"):
            return m.group(0)
        return f"url({_resolve(ref) or 'data:,'})"

    def _sub_import(m: re.Match) -> str:
        ref = m.group("ref")
        quote = m.group("q")
        if ref.strip().startswith("data:"):
            return m.group(0)
        return f"@import {quote}{_resolve(ref) or 'data:,'}{quote}"

    css_text = _CSS_URL_RE.sub(_sub_url, css_text)
    css_text = _CSS_IMPORT_STR_RE.sub(_sub_import, css_text)
    return css_text


# ── In-page DOM rewrite ────────────────────────────────────────────────────────
# Runs inside the rendered page via page.evaluate, once assetMap/cssMap are built, so every rewrite
# happens against the live (post-JS) DOM rather than the original markup. Strips every JS-execution
# and exfiltration surface, inlines what was captured, and neuters what wasn't.

_CAPTURE_JS = r"""
([assetMap, cssMap]) => {
  const REMOVE_SELECTOR = [
    'script', 'noscript', 'iframe', 'frame', 'object', 'embed', 'base',
    'link[rel~="preload"]', 'link[rel~="modulepreload"]', 'link[rel~="prefetch"]',
    'link[rel~="dns-prefetch"]', 'link[rel~="preconnect"]',
    'meta[http-equiv="refresh" i]',
  ].join(', ');
  document.querySelectorAll(REMOVE_SELECTOR).forEach((el) => el.remove());

  document.querySelectorAll('*').forEach((el) => {
    for (const attr of Array.from(el.attributes)) {
      if (/^on/i.test(attr.name)) el.removeAttribute(attr.name);
    }
    const href = el.getAttribute('href');
    if (href && /^\s*javascript:/i.test(href)) el.removeAttribute('href');
  });

  document.querySelectorAll('link[rel="stylesheet"]').forEach((link) => {
    const css = cssMap[link.href];
    if (css) {
      const style = document.createElement('style');
      style.textContent = css;
      link.replaceWith(style);
    } else {
      link.remove();
    }
  });

  const resolveAsset = (ref) => {
    try {
      return assetMap[new URL(ref, document.baseURI).href] || null;
    } catch (e) {
      return null;
    }
  };
  const rewriteCssUrls = (text) => (text || '').replace(
    /url\(\s*(['"]?)([^'")]*)\1\s*\)/gi,
    (m, q, ref) => {
      ref = ref.trim();
      if (ref.startsWith('data:')) return m;
      return `url(${resolveAsset(ref) || 'data:,'})`;
    },
  );

  document.querySelectorAll('style').forEach((style) => {
    style.textContent = rewriteCssUrls(style.textContent);
  });
  document.querySelectorAll('[style]').forEach((el) => {
    el.setAttribute('style', rewriteCssUrls(el.getAttribute('style')));
  });

  document.querySelectorAll('img').forEach((img) => {
    const ref = img.currentSrc || img.src;
    const data = ref ? assetMap[ref] : null;
    img.removeAttribute('srcset');
    img.removeAttribute('sizes');
    if (data) img.setAttribute('src', data); else img.removeAttribute('src');
  });
  document.querySelectorAll('picture > source, input[type="image"]').forEach((el) => {
    const ref = el.currentSrc || el.src;
    const data = ref ? assetMap[ref] : null;
    el.removeAttribute('srcset');
    el.removeAttribute('sizes');
    if (data) el.setAttribute('src', data); else el.removeAttribute('src');
  });
  document.querySelectorAll('video[poster]').forEach((v) => {
    const data = v.poster ? assetMap[v.poster] : null;
    if (data) v.setAttribute('poster', data); else v.removeAttribute('poster');
  });

  // Media itself (mp4, mp3, …) is never captured — drop src so nothing points back at the network.
  document.querySelectorAll('video, audio, track').forEach((el) => el.removeAttribute('src'));
  document.querySelectorAll('source').forEach((el) => {
    if (el.parentElement && el.parentElement.tagName === 'PICTURE') return;  // handled above
    el.removeAttribute('src');
    el.removeAttribute('srcset');
  });

  document.querySelectorAll('a[href]').forEach((a) => {
    try {
      a.setAttribute('href', new URL(a.getAttribute('href'), document.baseURI).href);
    } catch (e) { /* malformed href — leave it as-is */ }
  });

  const csp = document.createElement('meta');
  csp.setAttribute('http-equiv', 'Content-Security-Policy');
  csp.setAttribute(
    'content',
    "default-src 'none'; img-src data:; media-src data:; style-src 'unsafe-inline'; font-src data:;",
  );
  const head = document.head || document.documentElement;
  head.insertBefore(csp, head.firstChild);

  return '<!DOCTYPE html>\n' + document.documentElement.outerHTML;
}
"""


# ── Render pipeline ────────────────────────────────────────────────────────────

def render_capture(url: str, *, max_bytes: int, timeout: int = 30,
                    host_check=research._check_host_public) -> bytes:
    """Render `url` in headless Chromium and return one self-contained, script-stripped HTML
    document with images/fonts/stylesheets inlined as data URIs. Every subresource request the
    rendered page makes is re-checked by `_allow_request` (the SSRF guard, via route interception) —
    not just the URL passed in. Raises CaptureError if the rendered snapshot exceeds `max_bytes`;
    Playwright's own exceptions (navigation timeout, browser not installed) propagate to the caller
    (`try_render` catches them). The browser is always closed, even on failure."""
    from playwright.sync_api import sync_playwright

    cache: dict[str, bool] = {}

    def _handle_route(route):
        if _allow_request(route.request.url, cache, host_check):
            route.continue_()
        else:
            route.abort()

    responses = []

    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            # service_workers="block": a worker's fetches don't pass through context.route, so a
            # registered worker would be an unguarded egress path — refuse to run them at all.
            context = browser.new_context(user_agent=research._USER_AGENT, service_workers="block")
            context.route("**/*", _handle_route)
            # context.route never sees WebSocket connections; mock every one (a handler that never
            # calls connect_to_server) so a rendered page cannot open a socket past the SSRF guard.
            context.route_web_socket("**/*", lambda ws: None)
            page = context.new_page()
            page.on("response", lambda resp: responses.append(resp))

            page.goto(url, wait_until="load", timeout=timeout * 1000)
            try:
                page.wait_for_load_state("networkidle", timeout=5000)
            except Exception:
                pass  # long-polling SPAs never go idle — proceed with what's loaded

            assets: dict[str, str] = {}
            css_texts: dict[str, str] = {}
            total = 0
            for resp in responses:
                if 300 <= resp.status < 400:
                    continue
                ctype = (resp.headers.get("content-type") or "").split(";")[0].strip().lower()
                is_css = ctype == "text/css" or urlsplit(resp.url).path.lower().endswith(".css")
                if not (is_css or ctype.startswith("image/") or ctype.startswith("font/")):
                    continue
                try:
                    body = resp.body()
                except Exception:
                    continue
                if len(body) > _MAX_ASSET_BYTES:
                    continue
                if total + len(body) > max_bytes:
                    break  # stop harvesting past the deposit-wide cap; don't fail the capture
                total += len(body)
                if is_css:
                    css_texts[resp.url] = body.decode("utf-8", "replace")
                else:
                    b64 = base64.b64encode(body).decode("ascii")
                    assets[resp.url] = f"data:{ctype or 'application/octet-stream'};base64,{b64}"

            css_map = {href: rewrite_css(text, href, assets) for href, text in css_texts.items()}
            html = page.evaluate(_CAPTURE_JS, [assets, css_map])
        finally:
            browser.close()

    encoded = html.encode("utf-8")
    if len(encoded) > max_bytes:
        raise CaptureError(
            f"refused: rendered snapshot of {url} exceeds the {max_bytes // (1024 * 1024)} MiB cap")
    return encoded


def try_render(url: str, *, max_bytes: int, timeout: int = 30) -> bytes | None:
    """Best-effort rendered capture — the seam `research.deposit_one` uses and tests monkeypatch.
    Returns None (never raises) when Playwright isn't installed, Chromium isn't installed, navigation
    fails or times out, or the snapshot exceeds `max_bytes` — any of which means the caller should
    fall back to the plain, nh3-sanitized fetch instead."""
    try:
        from playwright.sync_api import Error as _PlaywrightError
    except ImportError:
        return None
    try:
        return render_capture(url, max_bytes=max_bytes, timeout=timeout)
    except (CaptureError, _PlaywrightError):
        return None
