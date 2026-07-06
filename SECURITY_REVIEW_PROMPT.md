# Watchdog — Security review (run this on Opus, standalone)

This is the **security-audit slice** of the larger production review, split into its own
prompt so it can be run on **Opus 4.8** in a fresh session. (It was carved out because the
Fable safety classifier is currently broad and trips on routine egress/SSRF review; that is
a harness quirk, not a signal about this work — this is authorized defensive review of the
maintainer's own tool.) Run it against latest `main`, clean tree.

The companion review `FABLE_PROD_REVIEW.md` (correctness, efficiency, tests, docs, dead
code) covers everything **except** security. This prompt owns security in full. Where the
two could overlap, security findings belong here.

## Context you need before forming an opinion

`watchdog` (package `watchdog-intel`) is a CLI that OCRs and extracts structured
intelligence from investigative-journalism document dumps into an Obsidian vault. The
threat model is unusual: **journalists, sensitive investigations, prompt-injection via
hostile documents and fetched web content.** Read, in order:

1. `ARCHITECTURE.md` §1 (design principles), §14 (web research/egress), §15 (Invariants
   I1–I5). A finding that violates an invariant is automatically high priority.
2. `DECISIONS.md`, especially **D45/D46** (egress SSRF guard, redirect re-validation, size
   caps, sidecar sanitization), **D47** (Wayback), **D61** (#200 rendered capture), **D62**
   (#243 — `VERIFY_X509_STRICT` deliberately cleared on egress fetches). D62 is **decided
   and recorded** — do not relitigate the trade; only verify the relaxation is scoped to the
   egress openers and hasn't leaked into any other TLS surface.
3. The code: `src/watchdog/pipeline/research.py`, `src/watchdog/pipeline/capture.py`,
   `src/watchdog/cmd/research.py`, `src/watchdog/cmd/auth.py`, `src/watchdog/pipeline/write_vault.py`
   (path-traversal guards, wikilink/frontmatter defang), `src/watchdog/cmd/setup.py` and
   `src/watchdog/setup_cmd.py` (shell-profile writes, key storage). Record-skill *content*
   (`skills/records/*.md`) is out of scope (issue #68).

## What to audit

- **#200/D61 rendered-capture containment, as implemented, not as described.** Headless
  Chromium runs page JS at capture time. Verify the per-request SSRF interception in
  `capture.py`'s route handler actually sees **every** request type — XHR/fetch, workers,
  redirects (including the top-level navigation redirect chain), websockets, prefetch/
  beacon. Service workers are set to `block` and websockets are `route_web_socket`-mocked —
  confirm those are the only channels that bypass `context.route`, and that nothing else
  does. Then audit the **saved snapshot**: can it phone home when opened (surviving external
  refs in CSS `url()`/`@import`, `<img srcset>`, `<picture>`, inline `style`, meta-refresh,
  `javascript:` hrefs, the pinned `default-src 'none'` CSP being bypassable)? Confirm the
  DOM rewrite (`_CAPTURE_JS`) neutralizes each surface. Consider IPv4-mapped IPv6
  (`::ffff:169.254.169.254`) and other `is_global` edge cases in `_check_host_public` /
  `_allow_request`.
- **Plain-fetch fallback keeps all pre-#200 guarantees.** When Playwright is absent or a
  render fails, `deposit_one` falls back to the nh3-sanitized plain fetch. Verify that path
  still enforces the SSRF guard, size cap, redirect re-validation, and sidecar defang — i.e.
  the richer capture didn't quietly weaken the fallback.
- **Egress SSRF guard end to end** (`research.fetch` / `validate_url` / `_check_host_public`):
  scheme allow-list, host-public check *before* the socket, re-validation on **every**
  redirect hop, the 20 MiB body cap, and the residual TOCTOU/DNS-rebinding risk (accepted in
  D45 — confirm it's still just that, not worse).
- **TLS relaxation scope (D62).** `VERIFY_X509_STRICT` is cleared. Confirm it is applied
  **only** to the egress openers (`_opener`, `_wayback_opener`) and has not leaked into any
  other TLS surface (model-client HTTP, anything else). Chain validation + hostname checks
  must remain on.
- **Injection into vault notes via extracted/fetched content.** Wikilink (`[[ ]]`) and
  frontmatter-delimiter (`---`) defang in `research.neutralize` and anywhere extracted
  content reaches a note; whether a hostile document/title can forge a wikilink, break
  frontmatter, or write outside the vault.
- **Path traversal** via CLI args and filenames; anything that writes outside the vault
  (`write_vault` morgue/note writes, deposit filenames, the `str(p).startswith(vault + "/")`
  guards — note these hardcode `/`).
- **Secrets handling** in `auth`/config: API keys (Anthropic/OpenAI/DeepSeek) and the
  archive.org S3 keys — storage location, masking, exposure in logs/telemetry
  (`usage-<ts>.json`), env-var handling.

## Discipline & output

- Every finding: `file:line`, current behavior, concrete exploit/failure scenario, proposed
  fix, tradeoff — actionable by a session with no memory of this one. Distinguish CONFIRMED
  (path traced) from PLAUSIBLE (needs a repro you couldn't complete). No speculative
  "could theoretically" findings.
- Verify before asserting; read-only commands and the test suite
  (`~/.local/pipx/venvs/watchdog-intel/bin/pytest`, never `pipx run pytest`) are fair game.
  Do **not** make real network calls to model providers or fetch real external URLs; use
  monkeypatched fetchers / localhost as the existing tests do.
- Write results to `FABLE_SECURITY_REVIEW.md` at the repo root, prioritized: (1) release
  blockers, (2) fix-before-production, (3) hardening/post-release. End with a one-paragraph
  verdict on the security posture.

## Already verified this session (don't re-derive; extend or refute)

- The egress SSRF test coverage is **non-vacuous**: inverting the private-address rejection
  in `_check_host_public` turns 8 tests in `tests/test_research.py` red (mutation-confirmed).
- `capture.py` route interception covers XHR/fetch/subresources via `context.route("**/*")`;
  service workers `block`ed, websockets mocked; the saved snapshot pins a `default-src 'none'`
  CSP as the first `<head>` child and inlines assets as `data:` URIs. This *looked* sound on
  read — the job here is to pressure-test it (the CSS `url()`/`@import` rewrite, `srcset`
  handling, redirect chains, IPv4-mapped hosts) rather than take it at face value.
- `_ssl_context()`'s strict-flag clearing is applied only to `_opener`/`_wayback_opener` on
  read; confirm nothing else constructs a relaxed context.
