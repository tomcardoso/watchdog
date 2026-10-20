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

// The Mac architectures to build. The release workflow builds each in its own job, so the two
// notarizations run at once rather than one after the other, and sets WATCHDOG_MAC_ARCH; a local
// build makes both. The arch has to be set here: electron-builder ignores --arm64/--x64 on the
// command line for a target whose arch the config names.
const macArch = process.env.WATCHDOG_MAC_ARCH ? [process.env.WATCHDOG_MAC_ARCH] : ['arm64', 'x64']

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
  // The in-app updater (src/main/updater.ts) reads a generic feed (.github/workflows/
  // app-release.yml): a stable build, the repository's latest release, through GitHub's
  // `releases/latest/download/` redirect (electron-updater follows it, for the latest*.yml and
  // the installer alike); a beta build, the rolling `app-beta` release, whose files each beta
  // replaces. The channel is a URL baked into the build (D297, D316): a beta build only ever sees
  // betas. Copies of 1.0.4
  // read the old rolling `app-stable` release, which stable releases keep current while it exists.
  //
  // detectUpdateChannel is off because the channel is the release URL: left on, a pre-release
  // version such as 0.1.0-beta.1 makes electron-builder write beta-mac.yml instead of
  // latest-mac.yml, and expect it, which the workflow's file list does not collect.
  detectUpdateChannel: false,
  publish: {
    provider: 'generic',
    url: channel === 'stable'
      ? 'https://github.com/tomcardoso/watchdog/releases/latest/download'
      : 'https://github.com/tomcardoso/watchdog/releases/download/app-beta'
  },
  mac: {
    category: 'public.app-category.productivity',
    // The .zip is what the updater downloads on macOS; the .dmg is for first installs.
    target: [
      { target: 'dmg', arch: macArch },
      { target: 'zip', arch: macArch }
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
    // The icons are placed by centre, measured from the top of the window's content area. Finder
    // shows the toolbar (and sometimes the path bar) above that area, so the visible height is
    // less than `height`; y sits the row about the middle of what is left.
    window: { width: 540, height: 300 },
    contents: [
      { x: 130, y: 115 },
      { x: 410, y: 115, type: 'link', path: '/Applications' }
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
