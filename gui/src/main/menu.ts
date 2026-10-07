// Native application menu. Items that act inside the app send a `menu.command` event to the
// renderer, which owns navigation and dialogs.

import { BrowserWindow, Menu, MenuItemConstructorOptions, app, shell } from 'electron'

export function buildMenu(getWindow: () => BrowserWindow | null): void {
  const send = (command: string) => () => getWindow()?.webContents.send('event', 'menu.command', { command })
  const isMac = process.platform === 'darwin'
  const template: MenuItemConstructorOptions[] = [
    ...(isMac
      ? [{
          label: app.name,
          submenu: [
            { role: 'about' },
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
        { label: 'Home', accelerator: 'CmdOrCtrl+1', click: send('go:home') },
        { label: 'Documents', accelerator: 'CmdOrCtrl+2', click: send('go:documents') },
        { label: 'Entities', accelerator: 'CmdOrCtrl+3', click: send('go:entities') },
        { label: 'Timeline', accelerator: 'CmdOrCtrl+4', click: send('go:timeline') },
        { label: 'Network', accelerator: 'CmdOrCtrl+5', click: send('go:graph') },
        { label: 'Review', accelerator: 'CmdOrCtrl+6', click: send('go:review') },
        { label: 'Ask', accelerator: 'CmdOrCtrl+7', click: send('go:ask') },
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
        { label: 'Watchdog Documentation', click: () => shell.openExternal('https://github.com/tomcardoso/watchdog/tree/main/docs') },
        { label: 'Report an Issue', click: () => shell.openExternal('https://github.com/tomcardoso/watchdog/issues') }
      ]
    }
  ]
  Menu.setApplicationMenu(Menu.buildFromTemplate(template))
}
