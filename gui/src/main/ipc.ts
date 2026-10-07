// IPC handlers behind the preload bridge (window.watchdog).

import { BrowserWindow, Notification, dialog, ipcMain, shell } from 'electron'
import { existsSync } from 'node:fs'
import { join } from 'node:path'
import { getPref, setPref } from './prefs'
import { PythonBackend, RpcError, bundledSource } from './python'
import { setAllowedRoots } from './protocol'
import { getThumb, putThumb } from './thumbs'
import { openTerminal } from './terminal'

export function registerIpc(backend: PythonBackend, getWindow: () => BrowserWindow | null): void {
  ipcMain.handle('rpc', async (_e, method: string, params: unknown) => {
    try {
      const result = await backend.call(method, params)
      if (method === 'projects.list') refreshRoots(result)
      return { ok: true, result }
    } catch (err) {
      const e = err as RpcError
      return { ok: false, error: { message: e.message, code: e.code ?? 'error', data: e.data ?? null } }
    }
  })

  ipcMain.handle('backend:status', () => backend.status)
  ipcMain.handle('backend:restart', async () => {
    const s = await backend.start()
    void loadRoots(backend)
    return s
  })
  ipcMain.handle('backend:choosePython', async () => {
    const win = getWindow()
    const r = await dialog.showOpenDialog(win!, {
      title: 'Choose the Python that has Watchdog installed',
      properties: ['openFile', 'showHiddenFiles']
    })
    if (r.canceled || !r.filePaths[0]) return backend.status
    await setPref('pythonPath', r.filePaths[0])
    const s = await backend.start()
    void loadRoots(backend)
    return s
  })

  ipcMain.handle('dialog:openFiles', async (_e, opts: { title?: string; folders?: boolean; multi?: boolean } = {}) => {
    const props: ('openFile' | 'openDirectory' | 'multiSelections')[] = [opts.folders ? 'openDirectory' : 'openFile']
    if (opts.multi !== false) props.push('multiSelections')
    if (!opts.folders && process.platform === 'darwin') props.push('openDirectory')
    const r = await dialog.showOpenDialog(getWindow()!, { title: opts.title ?? 'Choose files', properties: props })
    return r.canceled ? [] : r.filePaths
  })
  ipcMain.handle('dialog:openFolder', async (_e, opts: { title?: string } = {}) => {
    const r = await dialog.showOpenDialog(getWindow()!, { title: opts.title ?? 'Choose a folder', properties: ['openDirectory', 'createDirectory'] })
    return r.canceled ? null : r.filePaths[0] ?? null
  })
  ipcMain.handle('dialog:saveFile', async (_e, opts: { title?: string; defaultPath?: string } = {}) => {
    const r = await dialog.showSaveDialog(getWindow()!, { title: opts.title, defaultPath: opts.defaultPath })
    return r.canceled ? null : r.filePath ?? null
  })
  ipcMain.handle('dialog:confirm', async (_e, o: { title: string; message: string; detail?: string; confirm: string; destructive?: boolean }) => {
    const r = await dialog.showMessageBox(getWindow()!, {
      type: o.destructive ? 'warning' : 'question',
      title: o.title,
      message: o.message,
      detail: o.detail,
      buttons: [o.confirm, 'Cancel'],
      defaultId: o.destructive ? 1 : 0,
      cancelId: 1
    })
    return r.response === 0
  })

  ipcMain.handle('shell:openPath', (_e, p: string) => shell.openPath(p))
  ipcMain.on('shell:showItemInFolder', (_e, p: string) => shell.showItemInFolder(p))
  ipcMain.handle('shell:openExternal', (_e, url: string) => {
    if (/^(https?|mailto|obsidian):/i.test(url)) return shell.openExternal(url)
    return undefined
  })
  ipcMain.handle('shell:openInObsidian', async (_e, vault: string, note?: string) => {
    // Same URL the CLI's links.obsidian_url builds: obsidian://open?path=<absolute file>
    const target = note ? join(vault, note.endsWith('.md') || /\.[a-z0-9]{2,5}$/i.test(note) ? note : note + '.md') : vault
    try {
      await shell.openExternal('obsidian://open?path=' + encodeURIComponent(target))
      return true
    } catch {
      return false
    }
  })
  ipcMain.handle('shell:openTerminal', (_e, cwd: string, args: string[]) =>
    openTerminal(cwd, args, backend.status.python, bundledSource())
  )

  ipcMain.handle('thumbs:get', (_e, key: string) => getThumb(key))
  ipcMain.handle('thumbs:put', (_e, key: string, dataUrl: string) => putThumb(key, dataUrl))
  ipcMain.handle('prefs:get', (_e, key: string) => getPref(key))
  ipcMain.handle('prefs:set', (_e, key: string, value: unknown) => setPref(key, value))
  ipcMain.on('notify', (_e, title: string, body: string) => {
    const win = getWindow()
    if (win?.isFocused()) return
    if (Notification.isSupported()) new Notification({ title, body }).show()
  })
}

function refreshRoots(projects: unknown): void {
  if (!Array.isArray(projects)) return
  setAllowedRoots(projects.map((p: { path: string }) => p.path).filter((p) => p && existsSync(p)))
}

export async function loadRoots(backend: PythonBackend): Promise<void> {
  try {
    refreshRoots(await backend.call('projects.list', { all: true }))
  } catch {
    /* backend not ready; the renderer's first projects.list call fills this in */
  }
}
