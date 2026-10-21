# Distributing the Watchdog app

How the desktop app is built, signed, released and updated. Adapted from Sourcerer's process.

## Releases

- **The app**: a tag `v<semver>` runs `.github/workflows/app-release.yml`. `v1.0.5` is a stable
  release, `v1.1.0-beta.1` a beta. It tests the Python package and, at the same time, builds the
  app on macOS (Apple silicon and Intel, in separate jobs so their notarizations overlap), Windows
  and Linux with the tag's version. Once the tests and every build pass, it publishes:
  1. a stable release as the repository's **latest release**, titled "Watchdog 1.0.5". It is the
     page to send people (https://github.com/tomcardoso/watchdog/releases/latest) and the feed
     installed stable copies read for updates;
  2. a beta as a pre-release, titled "Watchdog 1.1.0-beta.1", whose files are also copied to the
     rolling pre-release `app-beta`, replacing the previous beta's. Beta builds read `app-beta`.

  The app carries the Python engine built from the same commit. The Python package is not
  published on its own: it is the engine the app bundles (#729). A stable tag must match
  `pyproject.toml`'s version, so the app and engine versions in Settings → About agree; the
  workflow stops before building otherwise. Releases up to 1.0.4 used `app-v*` tags (D297); the
  older `v0.1.0a*` and `v1.0.0`–`v1.0.3` tags are the Python package's.

  A build follows the channel it was released on, so a copy installed from a beta is never offered
  a stable release: it needs one manual download of the stable installer.

  **The `app-stable` release.** Copies of 1.0.4 read updates from a rolling release, `app-stable`.
  While it exists, each stable release replaces its files, so those copies update to the new
  version, after which they read the latest release like any other. The workflow never recreates
  it: once nobody runs 1.0.4, delete the release (and its tag) and the step skips itself.

## Releasing the app

1. Bump `version` in `pyproject.toml` to the new version (for a stable release), merge it to
   `main`, and make sure the commit you release is pushed.
2. Tag it and push the tag: `git tag v1.0.5 && git push origin v1.0.5`. For a beta, use
   `v1.1.0-beta.1`, with a higher beta number each time (`-beta.2`, `-beta.3`); the updater only
   offers a version higher than the one installed.

   Without git (from a phone, say), a stable release can be started instead from Actions → App
   release → Run workflow, on `main`, with **release** ticked. It releases `pyproject.toml`'s
   version and makes the tag itself, only after every build and test has passed (D319). It
   refuses to run on another branch, or when that version's tag already exists. Betas still need
   a pushed tag.
3. Watch the run under Actions → App release (usually 10 to 15 minutes, longer when GitHub has no
   Mac runner free; Apple's notarization is the slow step). Release notes list the merged pull requests since the previous release's tag.
4. Send people https://github.com/tomcardoso/watchdog/releases/latest (testers on the beta line:
   https://github.com/tomcardoso/watchdog/releases/tag/app-beta). A copy installed from there
   offers every later release on its channel under Check for Updates, and on its own shortly after
   launch.
5. Nothing to do for the docs' screenshots. After a stable release, the run's `screenshots` job
   takes the app screenshots again from the sample investigation, opens "Docs: app screenshots for
   <version>" against `main` and merges it itself (D320), then rebuilds the website. `main`'s
   ruleset requires a pull request but no approval, so the merge goes through; if it's ever
   refused, the job fails and the pull request stays open to merge by hand. A bad capture is undone
   by reverting that merge. It needs Settings → Actions → General → "Allow GitHub Actions to
   create and approve pull requests".

`gui/package.json`'s version is set by CI from the tag; it doesn't need bumping by hand.

To check a change builds on every platform before tagging, run the workflow by hand (Actions →
App release → Run workflow, on a branch): it builds and signs as a release does, keeps the
installers as the run's artifacts for five days, and publishes nothing. It takes the screenshots
too, and keeps them as the `screenshots` artifact rather than opening a pull request.

## Signing

Both platforms sign in CI when their secrets are set, and build unsigned otherwise. A local Mac
build is signed whenever a Developer ID Application certificate is in the keychain (electron-builder
finds it on its own), and notarized only when `APPLE_ID`, `APPLE_APP_SPECIFIC_PASSWORD` and
`APPLE_TEAM_ID` are set; without one it is unsigned. A local Windows build is unsigned.

**macOS** — signed with a Developer ID certificate and notarized by Apple. Repository secrets:
`APPLE_CERTIFICATE` (the .p12, base64-encoded), `APPLE_CERTIFICATE_PASSWORD`, `APPLE_ID`,
`APPLE_APP_SPECIFIC_PASSWORD` and `APPLE_TEAM_ID`. The hardened-runtime entitlements are in
`resources/entitlements.mac.plist`.

**Windows** — signed through Azure Artifact Signing, with the account (`sourcerer-signing`) and
certificate profile (`sourcerer-public`, East US endpoint) Sourcerer also uses; those names are in
`electron-builder.config.cjs`. Repository secrets: `AZURE_TENANT_ID`, `AZURE_CLIENT_ID`,
`AZURE_CLIENT_SECRET` (an app registration holding the Certificate Profile Signer role on that
profile) and `AZURE_PUBLISHER_NAME` (the certificate's subject name, exactly). Signing switches on
only when `AZURE_PUBLISHER_NAME` is set, so a build without it is unsigned rather than failing.

An unsigned macOS build opens only after the user allows it in System Settings → Privacy &
Security, and an unsigned Windows installer shows a SmartScreen warning; the in-app updater also
needs a signed macOS build to install updates.

## Updates

`src/main/updater.ts` uses electron-updater with a generic feed, set in
`electron-builder.config.cjs` from `WATCHDOG_RELEASE_CHANNEL` (`beta` unless `stable`) and baked
into the build's `app-update.yml`: `https://github.com/tomcardoso/watchdog/releases/latest/download/`
for a stable build, which GitHub redirects to the latest release's files, and
`https://github.com/tomcardoso/watchdog/releases/download/app-beta/` for a beta. It checks ten
seconds after launch and never downloads without being asked; the top bar shows the offer, the
download and "Restart to update". Check for Updates… (the Watchdog menu on a Mac, Help elsewhere) runs the same check and reports the
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

Only `dependencies` in `package.json` are copied into the installer, so that list holds just what
the main process loads from `node_modules` at run time (`electron-log`, `electron-updater`).
Everything the renderer uses is bundled into `out/renderer` by electron-vite and belongs in
`devDependencies` (D305). A package the main or preload process starts importing goes in
`dependencies`; otherwise the packaged app fails with "Cannot find module" though the dev build
and the E2E tests work.
