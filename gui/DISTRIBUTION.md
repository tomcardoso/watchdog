# Distributing the Watchdog app

How the desktop app is built, signed, released and updated. Adapted from Sourcerer's process.

## Two kinds of release

The app and the `watchdog` Python package are released separately, from the same repository:

- **The app**: a tag `app-v<semver>` runs `.github/workflows/app-release.yml`. `app-v0.2.0-beta.1`
  is a beta, `app-v0.2.0` a stable release. It tests the Python package, builds the app on macOS
  (Apple silicon and Intel), Windows and Linux with the tag's version, and publishes two GitHub
  releases:
  1. the versioned one, `app-v0.2.0-beta.1`, a pre-release for a beta, which keeps that version's
     files for good;
  2. the channel's rolling release, `app-beta` (or `app-stable`), whose files are replaced by each
     release on that channel. This is the page to send testers, and the feed installed copies read
     for updates.

  Neither is marked as the repository's latest release, and nothing goes to PyPI. The app carries
  the Python engine built from the same commit, whatever `pyproject.toml`'s version says.
- **The Python package**: a tag `v<pep440>` runs `.github/workflows/publish.yml` (tests, the wheel
  and sdist, a GitHub release with notes from the version-bump PR, and PyPI). It no longer builds
  the app. With the command line being retired (D267, #729), this path is expected to go.

## Releasing the app

1. Make sure the commit you release is pushed (on `electron`, for now).
2. Tag it and push the tag: `git tag app-v0.2.0-beta.1 && git push origin app-v0.2.0-beta.1`.
   Use a higher beta number each time (`-beta.2`, `-beta.3`); the updater only offers a version
   higher than the one installed.
3. Watch the run under Actions → App release (about 30 minutes; macOS notarization is the slow
   step). Release notes list the merged pull requests since the previous `app-v` tag.
4. Send testers https://github.com/tomcardoso/watchdog/releases/tag/app-beta. A copy installed from
   there offers every later beta under Help → Check for Updates, and on its own shortly after
   launch.

`gui/package.json`'s version is set by CI from the tag; it doesn't need bumping by hand.

## Signing

Both platforms sign in CI when their secrets are set, and build unsigned otherwise. A local Mac
build is signed whenever a Developer ID Application certificate is in the keychain (electron-builder
finds it on its own), and notarized only when `APPLE_ID`, `APPLE_APP_SPECIFIC_PASSWORD` and
`APPLE_TEAM_ID` are set; without one it is unsigned. A local Windows build is unsigned.

**macOS** — signed with a Developer ID certificate and notarized by Apple. Repository secrets:
`APPLE_CERTIFICATE` (the .p12, base64-encoded), `APPLE_CERTIFICATE_PASSWORD`, `APPLE_ID`,
`APPLE_APP_SPECIFIC_PASSWORD` and `APPLE_TEAM_ID`. The hardened-runtime entitlements are in
`resources/entitlements.mac.plist`.

**Windows** — signed through Azure Trusted Signing. Repository secrets: `AZURE_TENANT_ID`,
`AZURE_CLIENT_ID`, `AZURE_CLIENT_SECRET`, `AZURE_PUBLISHER_NAME`, `AZURE_SIGNING_ENDPOINT` (the
region's endpoint, such as `https://eus.codesigning.azure.net`), `AZURE_SIGNING_ACCOUNT` (the
Trusted Signing account name) and `AZURE_CERTIFICATE_PROFILE`. `electron-builder.config.cjs` signs
only when all four of the last ones are set, so a partial set builds unsigned rather than failing.

An unsigned macOS build opens only after the user allows it in System Settings → Privacy &
Security, and an unsigned Windows installer shows a SmartScreen warning; the in-app updater also
needs a signed macOS build to install updates.

## Updates

`src/main/updater.ts` uses electron-updater with a generic feed: the channel's rolling release,
`https://github.com/tomcardoso/watchdog/releases/download/app-beta/` (set in
`electron-builder.config.cjs` from `WATCHDOG_RELEASE_CHANNEL`, `beta` unless `stable`, and baked
into the build's `app-update.yml`). Not the GitHub provider: this repository's releases also carry
the Python package's `v1.x` tags, which that provider would read as app versions. It checks ten
seconds after launch and never downloads without being asked; the top bar shows the offer, the
download and "Restart to update". Help → Check for Updates… runs the same check and reports the
result. In development (or with `WATCHDOG_SIMULATE_UPDATES=1`) the whole flow is simulated, so the
interface can be tried without a release.

An update replaces the app and the Watchdog wheel it carries; on the next launch the engine sees
the newer wheel and reinstalls it (see `src/main/engine.ts`). macOS installs an update only into a
signed app; an unsigned Windows build updates, but shows SmartScreen's warning on first install.

## Building locally

```bash
npm ci
npm run dist:mac      # or dist:win, dist:linux — each on its own platform
```

Installers land in `dist/`. Cross-platform builds aren't supported: build each platform on that
platform, or let CI do it.
