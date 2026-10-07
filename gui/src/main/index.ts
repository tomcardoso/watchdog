// Electron main process: one window, the Python backend, and the bridge between them.

import { BrowserWindow, app, nativeTheme, screen, shell } from 'electron'
import { join } from 'node:path'
import { buildMenu } from './menu'
import { getPref, setPref } from './prefs'
import { handleProtocol, registerSchemePrivileges } from './protocol'
import { PythonBackend } from './python'
import { loadRoots, registerIpc } from './ipc'

registerSchemePrivileges()
app.setName('Watchdog')

let win: BrowserWindow | null = null

const backend = new PythonBackend(
  (event, data) => win?.webContents.send('event', event, data),
  (status) => win?.webContents.send('event', 'backend.status', status)
)

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
    backgroundColor: nativeTheme.shouldUseDarkColors ? '#141312' : '#f7f5f0',
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
  registerIpc(backend, () => win)
  buildMenu(() => win)
  await createWindow()
  await backend.start()
  void loadRoots(backend)
  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) void createWindow()
  })
})

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit()
})

app.on('before-quit', () => backend.stop())
