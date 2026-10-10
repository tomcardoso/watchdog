// electron-builder configuration, adapted from Sourcerer's. A .cjs file rather than YAML so that
// Windows signing can be switched on from the environment: CI sets AZURE_PUBLISHER_NAME (and the
// AZURE_TENANT_ID / AZURE_CLIENT_ID / AZURE_CLIENT_SECRET credentials) when the repository has the
// Azure Artifact Signing secrets, and every other build (local, or CI without them) is unsigned. macOS signing needs no switch here: electron-builder signs when a Developer ID
// certificate is in the keychain and notarizes when APPLE_ID, APPLE_APP_SPECIFIC_PASSWORD and
// APPLE_TEAM_ID are set (see DISTRIBUTION.md).

const azure = !!process.env.AZURE_PUBLISHER_NAME

// Which release channel this build follows for updates (see `publish` below): set by the release
// workflow from the tag; a local build follows the beta channel.
const channel = process.env.WATCHDOG_RELEASE_CHANNEL === 'stable' ? 'stable' : 'beta'

/** @type {import('electron-builder').Configuration} */
module.exports = {
  appId: 'com.github.tomcardoso.watchdog',
  productName: 'Watchdog',
  copyright: 'Copyright © Tom Cardoso',
  npmRebuild: false,
  directories: { output: 'dist', buildResources: 'resources' },
  files: ['out/**', '!out/**/*.map', 'package.json', 'resources/icon.png'],
  // The managed engine: the app installs Watchdog into a private Python environment on first run
  // (src/main/engine.ts). It needs two things from the package: the uv binary for the platform
  // being built (scripts/fetch-uv.mjs, into resources/bin/<os>-<arch>/) and a wheel of this
  // repository (scripts/build-wheel.mjs, into resources/python-wheel/). Both are produced by the
  // `predist*` npm scripts. `${os}` and `${arch}` are electron-builder's macros for the target.
  extraResources: [
    { from: 'resources/bin/${os}-${arch}', to: 'bin', filter: ['uv', 'uv.exe'] },
    { from: 'resources/python-wheel', to: 'python-wheel', filter: ['*.whl'] }
  ],
  asarUnpack: ['out/renderer/assets/*.mjs'],
  // The in-app updater (src/main/updater.ts) reads one rolling GitHub release per channel,
  // `app-beta` (or `app-stable`), whose files each app release replaces (.github/workflows/
  // app-release.yml). A generic feed rather than the GitHub provider, because this repository's
  // releases also carry the Python package's `v1.0.x` tags, which the GitHub provider would take
  // for app versions. The channel is baked into the build: a beta build only ever sees betas.
  //
  // detectUpdateChannel is off because the channel is the release URL: left on, a pre-release
  // version such as 0.1.0-beta.1 makes electron-builder write beta-mac.yml instead of
  // latest-mac.yml, and expect it, which the workflow's file list does not collect.
  detectUpdateChannel: false,
  publish: { provider: 'generic', url: `https://github.com/tomcardoso/watchdog/releases/download/app-${channel}` },
  mac: {
    category: 'public.app-category.productivity',
    // The .zip is what the updater downloads on macOS; the .dmg is for first installs.
    target: [
      { target: 'dmg', arch: ['arm64', 'x64'] },
      { target: 'zip', arch: ['arm64', 'x64'] }
    ],
    artifactName: 'Watchdog-${version}-${arch}.${ext}',
    hardenedRuntime: true,
    gatekeeperAssess: false,
    entitlements: 'resources/entitlements.mac.plist',
    entitlementsInherit: 'resources/entitlements.mac.plist',
    notarize: !!process.env.APPLE_TEAM_ID,
    darkModeSupport: true
  },
  dmg: {
    title: 'Watchdog',
    contents: [
      { x: 130, y: 220 },
      { x: 410, y: 220, type: 'link', path: '/Applications' }
    ]
  },
  win: {
    target: [{ target: 'nsis', arch: ['x64'] }],
    // The Artifact Signing account and certificate profile Sourcerer also signs with: the publisher
    // (the certificate's subject) is the same person, so one validated identity serves both apps.
    // Names and endpoint are not secrets; the credentials are.
    azureSignOptions: azure
      ? {
          publisherName: process.env.AZURE_PUBLISHER_NAME,
          endpoint: 'https://eus.codesigning.azure.net',
          codeSigningAccountName: 'sourcerer-signing',
          certificateProfileName: 'sourcerer-public'
        }
      : null
  },
  nsis: {
    oneClick: false,
    allowToChangeInstallationDirectory: true,
    artifactName: 'Watchdog-Setup-${version}.${ext}'
  },
  linux: {
    target: [{ target: 'AppImage', arch: ['x64'] }],
    artifactName: 'Watchdog-${version}.${ext}',
    category: 'Office',
    maintainer: 'Tom Cardoso'
  }
}
