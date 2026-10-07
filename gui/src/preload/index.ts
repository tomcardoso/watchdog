// The only surface the renderer has onto the machine: window.watchdog (see WatchdogBridge).

import { contextBridge, ipcRenderer, webUtils } from 'electron'
import type { BackendStatus, EngineStatus, WatchdogBridge } from '@shared/api'

type Listener = (data: unknown) => void
const listeners = new Map<string, Set<Listener>>()

ipcRenderer.on('event', (_e, event: string, data: unknown) => {
  listeners.get(event)?.forEach((cb) => {
    try {
      cb(data)
    } catch (err) {
      console.error(err)
    }
  })
})

class BridgeError extends Error {
  code: string
  data: unknown
  constructor(message: string, code: string, data: unknown) {
    super(message)
    this.code = code
    this.data = data
  }
}

const bridge: WatchdogBridge = {
  async rpc(method, params) {
    const r = await ipcRenderer.invoke('rpc', method, params ?? {})
    if (r.ok) return r.result
    throw new BridgeError(r.error.message, r.error.code, r.error.data)
  },
  on(event, cb) {
    let set = listeners.get(event)
    if (!set) listeners.set(event, (set = new Set()))
    set.add(cb as Listener)
    return () => set!.delete(cb as Listener)
  },
  backend: {
    status: () => ipcRenderer.invoke('backend:status') as Promise<BackendStatus>,
    restart: () => ipcRenderer.invoke('backend:restart') as Promise<BackendStatus>,
    choosePython: () => ipcRenderer.invoke('backend:choosePython') as Promise<BackendStatus>
  },
  engine: {
    status: () => ipcRenderer.invoke('engine:status') as Promise<EngineStatus>,
    install: (opts) => ipcRenderer.invoke('engine:install', opts ?? {}) as Promise<EngineStatus>,
    cancel: () => ipcRenderer.invoke('engine:cancel') as Promise<void>,
    reinstall: () => ipcRenderer.invoke('engine:reinstall') as Promise<EngineStatus>
  },
  claude: {
    status: () => ipcRenderer.invoke('claude:status'),
    signIn: () => ipcRenderer.invoke('claude:signIn'),
    cancel: () => ipcRenderer.invoke('claude:cancel')
  },
  dialog: {
    openFiles: (opts) => ipcRenderer.invoke('dialog:openFiles', opts),
    openFolder: (opts) => ipcRenderer.invoke('dialog:openFolder', opts),
    saveFile: (opts) => ipcRenderer.invoke('dialog:saveFile', opts),
    confirm: (opts) => ipcRenderer.invoke('dialog:confirm', opts)
  },
  shell: {
    openPath: (p) => ipcRenderer.invoke('shell:openPath', p),
    showItemInFolder: (p) => ipcRenderer.send('shell:showItemInFolder', p),
    openExternal: (url) => ipcRenderer.invoke('shell:openExternal', url),
    openInObsidian: (vault, note) => ipcRenderer.invoke('shell:openInObsidian', vault, note)
  },
  files: {
    pathForFile: (file) => webUtils.getPathForFile(file),
    url: (absPath) => 'wdfile://local/' + encodeURIComponent(absPath)
  },
  thumbs: {
    get: (key) => ipcRenderer.invoke('thumbs:get', key),
    put: (key, dataUrl) => ipcRenderer.invoke('thumbs:put', key, dataUrl)
  },
  prefs: {
    get: (key) => ipcRenderer.invoke('prefs:get', key),
    set: (key, value) => ipcRenderer.invoke('prefs:set', key, value)
  },
  notify: (title, body) => ipcRenderer.send('notify', title, body),
  access: {
    list: () => ipcRenderer.invoke('access:list'),
    request: (path, label, why) => ipcRenderer.invoke('access:request', path, label, why),
    revoke: (path) => ipcRenderer.invoke('access:revoke', path)
  },
  updates: {
    get: () => ipcRenderer.invoke('update:get'),
    check: () => ipcRenderer.invoke('update:check'),
    download: () => ipcRenderer.invoke('update:download'),
    install: () => ipcRenderer.invoke('update:install')
  },
  platform: process.platform
}

contextBridge.exposeInMainWorld('watchdog', bridge)
