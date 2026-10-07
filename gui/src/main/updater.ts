// In-app updates from GitHub Releases (electron-updater), adapted from Sourcerer's updater.
//
// The app checks quietly ten seconds after launch and never downloads without being asked: the top
// bar offers "Update available", then shows the download, then "Restart to update". Help → Check
// for Updates… runs the same check and, unlike the background one, reports "up to date" and
// failures. In development, or in a build that isn't installed, a check simulates the whole flow
// so the interface can be exercised. An update replaces the app and the Watchdog engine source it
// carries; the engine reinstalls itself from the new version on the next launch (engine.ts).

import { BrowserWindow, app, dialog, ipcMain } from 'electron'
import log from 'electron-log/main'
import { autoUpdater } from 'electron-updater'

export interface UpdateState {
  state: 'idle' | 'available' | 'downloading' | 'ready'
  version: string | null
  percent: number | null
  error: string | null
}

let current: UpdateState = { state: 'idle', version: null, percent: null, error: null }
let userInitiated = false
const simulated = () => !app.isPackaged || process.env.WATCHDOG_SIMULATE_UPDATES === '1'

function publish(patch: Partial<UpdateState>): void {
  current = { ...current, error: null, ...patch }
  // Resolve the window at send time: on macOS it can be closed and re-created while the app runs.
  for (const win of BrowserWindow.getAllWindows()) win.webContents.send('event', 'update.state', current)
}

function simulateCheck(): void {
  publish({ state: 'available', version: '99.0.0', percent: null })
}

let simulating = false
function simulateDownload(): Promise<void> {
  if (simulating) return Promise.resolve()
  simulating = true
  return new Promise((resolve) => {
    ;[20, 45, 70, 100].forEach((percent, i) =>
      setTimeout(() => {
        publish({ state: 'downloading', percent })
        if (percent === 100) {
          setTimeout(() => {
            simulating = false
            publish({ state: 'ready', percent: null })
            resolve()
          }, 300)
        }
      }, (i + 1) * 450)
    )
  })
}

/** Help → Check for Updates…: a check that reports its result, unlike the background one. */
export function checkForUpdatesFromMenu(): void {
  if (simulated()) {
    simulateCheck()
    return
  }
  userInitiated = true
  autoUpdater.checkForUpdates().catch(() => undefined)
}

export function registerUpdater(): void {
  log.initialize()
  autoUpdater.logger = log
  autoUpdater.autoDownload = false
  autoUpdater.autoInstallOnAppQuit = false

  ipcMain.handle('update:get', () => current)
  ipcMain.handle('update:check', () => (simulated() ? simulateCheck() : autoUpdater.checkForUpdates().then(() => undefined)))
  ipcMain.handle('update:download', () => {
    if (simulated()) return simulateDownload()
    // Mark the download as started at once, so an error before the first progress event is
    // reported as a download failure rather than a failed check.
    publish({ state: 'downloading', percent: 0 })
    return autoUpdater.downloadUpdate().then(() => undefined)
  })
  ipcMain.handle('update:install', async () => {
    if (simulated()) {
      await dialog.showMessageBox({ type: 'info', title: 'Simulated update', message: 'In an installed build, Watchdog would now restart into the new version.' })
      return
    }
    // Squirrel.Mac can refuse quitAndInstall for a moment after update-downloaded fires ("command
    // is disabled"); retry that one error with backoff and fail fast on anything else.
    let delay = 200
    for (let i = 0; i < 8; i++) {
      try {
        autoUpdater.quitAndInstall(false, true)
        return
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err)
        if (!msg.includes('command is disabled')) {
          log.error('quitAndInstall failed:', err)
          break
        }
        await new Promise((r) => setTimeout(r, delay))
        delay = Math.min(delay * 2, 2000)
      }
    }
    publish({ state: 'ready', error: 'Watchdog could not restart into the update. Quit and reopen it to finish updating.' })
  })

  if (simulated()) return

  autoUpdater.on('update-available', (info) => {
    userInitiated = false
    publish({ state: 'available', version: info.version, percent: null })
  })
  autoUpdater.on('update-not-available', () => {
    if (!userInitiated) return
    userInitiated = false
    void dialog.showMessageBox({ type: 'info', title: 'No updates available', message: `Watchdog ${app.getVersion()} is the latest version.` })
  })
  autoUpdater.on('download-progress', (p) => publish({ state: 'downloading', percent: Math.round(p.percent) }))
  autoUpdater.on('update-downloaded', (info) => publish({ state: 'ready', version: info.version, percent: null }))
  // electron-updater reports failures through this event, not by rejecting its promises.
  autoUpdater.on('error', (err) => {
    const message = err instanceof Error ? err.message : String(err)
    const wasUser = userInitiated
    userInitiated = false
    if (current.state === 'downloading' || current.state === 'ready') {
      publish({ state: 'available', percent: null, error: 'The update could not be downloaded. Try again in a moment.' })
    } else if (wasUser) {
      void dialog.showMessageBox({ type: 'error', title: 'Update check failed', message: 'Watchdog could not check for updates. Try again later.', detail: message })
    }
    log.warn('update error:', message) // background check failures stay quiet
  })

  setTimeout(() => autoUpdater.checkForUpdates().catch(() => undefined), 10_000)
}
