# Distributing the Watchdog app

How the desktop app is built, signed, released and updated. Adapted from Sourcerer's process.

## One version for everything

The app and the `watchdog` Python package ship together from one tag. A `v1.4.0` tag runs
`.github/workflows/publish.yml`, which:

1. tests the Python package and builds its wheel and sdist;
2. builds the app on macOS (Apple silicon and Intel), Windows and Linux, setting the app's version
   from the tag (a PEP 440 pre-release such as `1.4.0b1` becomes `1.4.0-beta.1` for the app);
3. creates the GitHub Release, with notes taken from the version-bump PR's "What's new" section and
   the download links in `.github/release-template.md`, and attaches the installers and the files
   the updater reads (`latest*.yml`, `*.blockmap`, the macOS `.zip`);
4. publishes the Python package to PyPI.

So the release that the in-app updater finds is always the one that carries the matching engine.

## Releasing

Follow the release steps in the repository's `CLAUDE.md` (a `chore/v<version>` branch that bumps
`pyproject.toml`, a PR with a "What's new" section, then the tag). `gui/package.json`'s version is
set by CI from the tag; it doesn't need bumping by hand.

## Signing

Both platforms sign in CI when their secrets are set, and build unsigned otherwise. Local builds
are unsigned.

**macOS** — signed with a Developer ID certificate and notarized by Apple. Repository secrets:
`APPLE_CERTIFICATE` (the .p12, base64-encoded), `APPLE_CERTIFICATE_PASSWORD`, `APPLE_ID`,
`APPLE_APP_SPECIFIC_PASSWORD` and `APPLE_TEAM_ID`. The hardened-runtime entitlements are in
`resources/entitlements.mac.plist`.

**Windows** — signed through Azure Trusted Signing. Repository secrets: `AZURE_TENANT_ID`,
`AZURE_CLIENT_ID`, `AZURE_CLIENT_SECRET` and `AZURE_PUBLISHER_NAME`; the signing account and
certificate profile names are in `electron-builder.config.cjs`.

An unsigned macOS build opens only after the user allows it in System Settings → Privacy &
Security, and an unsigned Windows installer shows a SmartScreen warning; the in-app updater also
needs a signed macOS build to install updates.

## Updates

`src/main/updater.ts` uses electron-updater against this repository's GitHub Releases. It checks
ten seconds after launch and never downloads without being asked; the top bar shows the offer, the
download and "Restart to update". Help → Check for Updates… runs the same check and reports the
result. In development (or with `WATCHDOG_SIMULATE_UPDATES=1`) the whole flow is simulated, so the
interface can be tried without a release.

An update replaces the app and the Watchdog wheel it carries; on the next launch the engine sees
the newer wheel and reinstalls it (see `src/main/engine.ts`).

## Building locally

```bash
npm ci
npm run dist:mac      # or dist:win, dist:linux — each on its own platform
```

Installers land in `dist/`. Cross-platform builds aren't supported: build each platform on that
platform, or let CI do it.
