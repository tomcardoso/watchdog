// Native application menu. Items that act inside the app send a `menu.command` event to the
// renderer, which owns navigation and dialogs.

import { BrowserWindow, Menu, MenuItemConstructorOptions, app, shell } from 'electron'
import log from 'electron-log/main'
import { checkForUpdatesFromMenu } from './updater'

export function buildMenu(getWindow: () => BrowserWindow | null): void {
  const send = (command: string) => () => getWindow()?.webContents.send('event', 'menu.command', { command })
  const isMac = process.platform === 'darwin'
  const template: MenuItemConstructorOptions[] = [
    ...(isMac
      ? [{
          label: app.name,
          submenu: [
            { role: 'about' },
            { label: 'Check for Updates…', click: () => checkForUpdatesFromMenu() },
            { type: 'separator' },
            { label: 'Settings…', accelerator: 'Cmd+,', click: send('settings') },
            { type: 'separator' },
            { role: 'services' },
            { type: 'separator' },
            { role: 'hide' },
            { role: 'hideOthers' },
            { role: 'unhide' },
            { type: 'separator' },
            { role: 'quit' }
          ]
        } as MenuItemConstructorOptions]
      : []),
    {
      label: 'File',
      submenu: [
        { label: 'New Investigation…', accelerator: 'CmdOrCtrl+Shift+N', click: send('new-investigation') },
        { label: 'Open Investigation…', accelerator: 'CmdOrCtrl+O', click: send('switch-investigation') },
        { type: 'separator' },
        { label: 'Add Documents…', accelerator: 'CmdOrCtrl+Shift+A', click: send('add-documents') },
        { label: 'Fetch Links…', click: send('fetch-links') },
        { type: 'separator' },
        { label: 'Show in Folder', click: send('show-folder') },
        { label: 'Open in Obsidian', click: send('open-obsidian') },
        ...(isMac ? [] : [{ type: 'separator' } as MenuItemConstructorOptions, { label: 'Settings', accelerator: 'Ctrl+,', click: send('settings') }, { role: 'quit' } as MenuItemConstructorOptions])
      ]
    },
    { role: 'editMenu' },
    {
      label: 'View',
      submenu: [
        { label: 'Command Palette…', accelerator: 'CmdOrCtrl+K', click: send('palette') },
        { label: 'Search', accelerator: 'CmdOrCtrl+F', click: send('search') },
        { type: 'separator' },
        // One shortcut per Investigation screen, in sidebar order; Ctrl+Tab reaches the rest.
        { label: 'Overview', accelerator: 'CmdOrCtrl+1', click: send('go:home') },
        { label: 'Documents', accelerator: 'CmdOrCtrl+2', click: send('go:documents') },
        { label: 'Entities', accelerator: 'CmdOrCtrl+3', click: send('go:entities') },
        { label: 'Timeline', accelerator: 'CmdOrCtrl+4', click: send('go:timeline') },
        { label: 'Network', accelerator: 'CmdOrCtrl+5', click: send('go:graph') },
        // Shown for discoverability only: the window handles Ctrl+Tab itself (App.tsx useShortcuts).
        { label: 'Next Sidebar Item', accelerator: 'Ctrl+Tab', registerAccelerator: false, click: send('nav-next') },
        { label: 'Previous Sidebar Item', accelerator: 'Ctrl+Shift+Tab', registerAccelerator: false, click: send('nav-prev') },
        { type: 'separator' },
        { role: 'reload' },
        { role: 'toggleDevTools' },
        { type: 'separator' },
        { role: 'resetZoom' },
        { role: 'zoomIn' },
        { role: 'zoomOut' },
        { type: 'separator' },
        { role: 'togglefullscreen' }
      ]
    },
    { role: 'windowMenu' },
    {
      role: 'help',
      submenu: [
        { label: 'Take the Tour', click: send('take-tour') },
        { type: 'separator' },
        { label: 'Watchdog Documentation', click: () => shell.openExternal('https://tomcardoso.github.io/watchdog/getting-started.html') },
        { label: 'Report an Issue', click: () => shell.openExternal('https://github.com/tomcardoso/watchdog/issues') },
        { label: 'Show Log File', click: () => shell.showItemInFolder(log.transports.file.getFile().path) },
        ...(isMac ? [] : [
          { type: 'separator' } as MenuItemConstructorOptions,
          { label: 'Check for Updates…', click: () => checkForUpdatesFromMenu() },
          { role: 'about' } as MenuItemConstructorOptions
        ])
      ]
    }
  ]
  Menu.setApplicationMenu(Menu.buildFromTemplate(template))
}
