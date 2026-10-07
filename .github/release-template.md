## What's new

<!-- Describe changes in this release -->

---

## Installation

Download the Watchdog app for your computer. It sets up everything else it needs the first time it opens.

### macOS
- **[Apple silicon (M1 and later)](https://github.com/tomcardoso/watchdog/releases/download/v{version}/Watchdog-{version}-arm64.dmg)**
- **[Intel](https://github.com/tomcardoso/watchdog/releases/download/v{version}/Watchdog-{version}-x64.dmg)**

Open the `.dmg` and drag Watchdog to your Applications folder.

### Windows
**[Download Watchdog-Setup-{version}.exe](https://github.com/tomcardoso/watchdog/releases/download/v{version}/Watchdog-Setup-{version}.exe)**

Run the installer.

### Linux
**[Download Watchdog-{version}.AppImage](https://github.com/tomcardoso/watchdog/releases/download/v{version}/Watchdog-{version}.AppImage)**

Make it executable (`chmod +x Watchdog-{version}.AppImage`) and run it.

### Updating

If Watchdog is already installed, it tells you when an update is ready: click **Update available** at the top of the window, then **Restart to update** once it has downloaded.

### The command line (being retired)

The `watchdog` command is still published to PyPI for scripts and automation: `pipx install "watchdog-intel=={version}"`. New users should use the app.

---

## Resources

- [Getting started](https://github.com/tomcardoso/watchdog/blob/main/docs/getting-started.md)
- [Install guide](https://github.com/tomcardoso/watchdog/blob/main/docs/install.md)
- [Report an issue](https://github.com/tomcardoso/watchdog/issues)

---

<details>
<summary>About the other files</summary>

Files ending in `.blockmap` and `latest*.yml` are read by the app's updater and must stay attached to the release. The `.zip` files are the macOS update packages. To install Watchdog by hand you only need the `.dmg` (macOS), `.exe` (Windows) or `.AppImage` (Linux).

</details>

