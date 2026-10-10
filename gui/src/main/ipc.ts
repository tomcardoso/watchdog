// IPC handlers behind the preload bridge (window.watchdog).

import { BrowserWindow, Notification, dialog, ipcMain, shell } from 'electron'
import { existsSync } from 'node:fs'
import { join } from 'node:path'
import { getPref, setPref } from './prefs'
import { PythonBackend, RpcError } from './python'
import type { EngineStatus } from '@shared/api'
import { Engine } from './engine'
import { ClaudeSignIn } from './claude'
import { setAllowedRoots } from './protocol'
import { addGrant, listGrants, requestGrant, revokeGrant } from './access'
import { getThumb, putThumb } from './thumbs'
import { prepareRequest } from './secrets'

export function registerIpc(backend: PythonBackend, engine: Engine, claude: ClaudeSignIn, getWindow: () => BrowserWindow | null): void {
  ipcMain.handle('rpc', async (_e, method: string, params: unknown) => {
    try {
      // A pasted key is encrypted here before the backend sees it to store (D295).
      const result = await backend.call(method, prepareRequest(method, params))
      if (method === 'projects.list') refreshRoots()
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

  ipcMain.handle('engine:status', () => engine.status())
  ipcMain.handle('engine:install', (_e, opts: { fresh?: boolean } = {}) => runInstall(backend, engine, !!opts.fresh))
  ipcMain.handle('engine:reinstall', () => runInstall(backend, engine, true))
  ipcMain.handle('engine:cancel', () => engine.cancel())
  ipcMain.handle('claude:status', () => claude.status())
  ipcMain.handle('claude:signIn', () => claude.signIn())
  ipcMain.handle('claude:cancel', () => claude.cancel())

  ipcMain.handle('dialog:openFiles', async (_e, opts: { title?: string; folders?: boolean; multi?: boolean } = {}) => {
    const props: ('openFile' | 'openDirectory' | 'multiSelections')[] = [opts.folders ? 'openDirectory' : 'openFile']
    if (opts.multi !== false) props.push('multiSelections')
    if (!opts.folders && process.platform === 'darwin') props.push('openDirectory')
    const r = await dialog.showOpenDialog(getWindow()!, { title: opts.title ?? 'Choose files', properties: props })
    return r.canceled ? [] : r.filePaths
  })
  // `grant` names why the folder is wanted when Watchdog will change files in it (a home for new
  // investigations, an existing investigation, a move or export destination). Choosing a folder in
  // the system dialog for that stated purpose is the user's permission, as on macOS.
  ipcMain.handle('dialog:openFolder', async (_e, opts: { title?: string; grant?: string } = {}) => {
    const r = await dialog.showOpenDialog(getWindow()!, {
      title: opts.title ?? 'Choose a folder',
      message: opts.grant ? `Watchdog will be allowed to read and change files in the folder you choose (${opts.grant}).` : undefined,
      properties: ['openDirectory', 'createDirectory']
    })
    const picked = r.canceled ? null : r.filePaths[0] ?? null
    if (picked && opts.grant) {
      addGrant(picked, opts.grant)
      refreshRoots()
    }
    return picked
  })

  // Folder access (main/access.ts). The renderer can list and revoke, and can ask — but a grant
  // outside a folder dialog always goes through a native prompt the renderer can't answer itself.
  ipcMain.handle('access:list', () => listGrants())
  ipcMain.handle('access:request', async (_e, path: string, label: string, why: string) => {
    const ok = await requestGrant(getWindow(), path, label, why)
    if (ok) refreshRoots()
    return ok
  })
  ipcMain.handle('access:revoke', (_e, path: string) => {
    const out = revokeGrant(path)
    refreshRoots()
    return out
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

/**
 * Install, repair or finish the managed engine (engine.ts). Progress arrives as 'engine.progress'
 * events; this resolves with the final status. The backend is stopped only when phase 1 has work
 * to do (the files it runs from are replaced), and started again as soon as phase 1 is in place,
 * so the app is usable while phase 2 continues. When phase 2 finishes the running backend is told
 * (`engine.setReady`) rather than restarted, so an open Ask Claude conversation is not cut off.
 */
export async function runInstall(backend: PythonBackend, engine: Engine, fresh: boolean): Promise<EngineStatus> {
  if (engine.isRunning()) return engine.status()
  const external = !fresh && backend.status.state === 'ready' && engine.externalPython ? engine.externalPython : null
  const coreWork = !external && (fresh || engine.engineState() !== 'ready' || backend.status.state !== 'ready')
  if (coreWork) await backend.suspend(fresh ? 'Reinstalling the engine…' : 'Installing the engine…')
  const state = await engine.install({
    external,
    fresh,
    onCore: async () => {
      if (!coreWork) return
      await backend.start()
      void loadRoots(backend)
    }
  })
  if (state === 'done') await backend.engineReady()
  return engine.status()
}

/** At launch: finish an engine whose background phase did not complete (the app was quit, the
 * network dropped, or an update brought a new version), without asking. */
export async function resumeEngine(backend: PythonBackend, engine: Engine): Promise<void> {
  if (backend.status.state !== 'ready') {
    if (backend.status.state !== 'starting') return
    try {
      await backend.waitReady()
    } catch {
      return
    }
  }
  const onManaged = backend.status.state === 'ready' && (backend.status.source === 'managed' || !!engine.simulate)
  if (onManaged && engine.needsBackground()) void runInstall(backend, engine, false)
}

function refreshRoots(): void {
  // wdfile:// serves files only from folders the user has allowed.
  setAllowedRoots(listGrants().map((g) => g.path).filter((p) => p && existsSync(p)))
}

export async function loadRoots(_backend?: PythonBackend): Promise<void> {
  refreshRoots()
}
