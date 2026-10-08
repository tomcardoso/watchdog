// Electron main process: one window, the Python backend, and the bridge between them.

import { BrowserWindow, app, nativeTheme, screen, shell } from 'electron'
import { join } from 'node:path'
import { buildMenu } from './menu'
import { getPref, setPref } from './prefs'
import { handleProtocol, registerSchemePrivileges } from './protocol'
import { PythonBackend, bundledSource } from './python'
import { Engine } from './engine'
import { ClaudeSignIn } from './claude'
import { spawnSync } from 'node:child_process'
import { existsSync } from 'node:fs'
import { loadRoots, registerIpc, resumeEngine } from './ipc'
import { registerUpdater } from './updater'

registerSchemePrivileges()
app.setName('Watchdog')

// One copy of the app at a time: two would run two backends over the same investigations and race
// on the same files. A second launch focuses the window that's already open.
if (!app.requestSingleInstanceLock()) app.quit()
app.on('second-instance', () => {
  if (!win) return
  if (win.isMinimized()) win.restore()
  win.focus()
})

let win: BrowserWindow | null = null

const send = (event: string, data: unknown) => win?.webContents.send('event', event, data)

const engine = new Engine({
  userData: app.getPath('userData'),
  // Packaged: electron-builder puts bin/ and python-wheel/ under process.resourcesPath.
  resources: app.isPackaged ? process.resourcesPath : join(__dirname, '..', '..', 'resources'),
  isPackaged: app.isPackaged,
  repoRoot: existsSync(join(__dirname, '..', '..', '..', 'pyproject.toml')) ? join(__dirname, '..', '..', '..') : null,
  emit: (e) => send('engine.progress', e)
})

const backend = new PythonBackend((event, data) => send(event, data), (status) => send('backend.status', status), engine)

/** Path to the Claude Code program inside the engine's claude-agent-sdk, asked of the running Python. */
const cliCache = new Map<string, string | null>()
function claudeCli(): string | null {
  const python = backend.status.python ?? engine.managedPython()
  if (!python) return null
  if (cliCache.has(python)) return cliCache.get(python)!
  const found = lookupClaudeCli(python)
  if (found) cliCache.set(python, found)
  return found
}
function lookupClaudeCli(python: string): string | null {
  const r = spawnSync(python, ['-m', 'watchdog.gui.engine_setup', 'claude-path'], {
    encoding: 'utf8',
    timeout: 30000,
    env: { ...process.env, ...(engine.isDev && bundledSource() ? { PYTHONPATH: bundledSource()! } : {}) }
  })
  return r.status === 0 && r.stdout.trim() ? r.stdout.trim() : null
}
const claude = new ClaudeSignIn(claudeCli, (url) => send('claude.signin', { url }), () => process.env, !!engine.simulate)

interface Bounds { x?: number; y?: number; width: number; height: number; maximized?: boolean }

async function createWindow(): Promise<void> {
  const saved = (await getPref<Bounds>('windowBounds')) ?? { width: 1440, height: 920 }
  const area = screen.getPrimaryDisplay().workAreaSize
  win = new BrowserWindow({
    width: Math.min(saved.width, area.width),
    height: Math.min(saved.height, area.height),
    x: saved.x,
    y: saved.y,
    minWidth: 980,
    minHeight: 640,
    show: false,
    title: 'Watchdog',
    // macOS and Windows take the icon from the app bundle; Linux window managers need it here.
    icon: process.platform === 'linux' ? join(app.getAppPath(), 'resources', 'icon.png') : undefined,
    // --bg from the renderer's tokens.css, so the window doesn't flash another colour while it loads.
    backgroundColor: nativeTheme.shouldUseDarkColors ? '#111113' : '#f4f4f5',
    titleBarStyle: process.platform === 'darwin' ? 'hiddenInset' : 'default',
    trafficLightPosition: { x: 16, y: 18 },
    webPreferences: {
      preload: join(__dirname, '../preload/index.js'),
      sandbox: false,
      contextIsolation: true,
      nodeIntegration: false,
      spellcheck: true
    }
  })
  if (saved.maximized) win.maximize()
  win.once('ready-to-show', () => win?.show())

  const save = () => {
    if (!win) return
    const b = win.getNormalBounds()
    void setPref('windowBounds', { ...b, maximized: win.isMaximized() })
  }
  win.on('resize', save)
  win.on('move', save)
  win.on('closed', () => (win = null))

  // Links to the web open in the browser, never inside the app window.
  win.webContents.setWindowOpenHandler(({ url }) => {
    if (/^https?:/i.test(url)) void shell.openExternal(url)
    return { action: 'deny' }
  })
  win.webContents.on('will-navigate', (e, url) => {
    if (!url.startsWith('http://localhost') && !url.startsWith('file:')) {
      e.preventDefault()
      if (/^https?:/i.test(url)) void shell.openExternal(url)
    }
  })

  if (process.env.ELECTRON_RENDERER_URL) await win.loadURL(process.env.ELECTRON_RENDERER_URL)
  else await win.loadFile(join(__dirname, '../renderer/index.html'))
}

app.whenReady().then(async () => {
  handleProtocol()
  app.setAboutPanelOptions({
    applicationName: 'Watchdog',
    applicationVersion: app.getVersion(),
    website: 'https://github.com/tomcardoso/watchdog',
    copyright: `© ${new Date().getFullYear()} Tom Cardoso. MIT licence.`
  })
  registerIpc(backend, engine, claude, () => win)
  registerUpdater()
  buildMenu(() => win)
  await createWindow()
  await backend.start()
  void loadRoots(backend)
  // An engine whose background phase was interrupted (the app quit, the network dropped) or that an
  // update has replaced finishes in the background, without asking (D272).
  void resumeEngine(backend, engine)
  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) void createWindow()
  })
})

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit()
})

app.on('before-quit', () => {
  // A download in progress stops with the app and resumes at the next launch; left running, it
  // would race the next launch's own run over the same environment.
  engine.cancel()
  backend.stop()
})
