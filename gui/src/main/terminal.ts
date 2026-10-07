// Opens the system terminal running a `watchdog` command — the fallback for the interactive
// Claude Code sessions (`watchdog ask`, `watchdog research`) for anyone who prefers the terminal.

import { spawn } from 'node:child_process'
import { existsSync, writeFileSync, chmodSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

function shellQuote(s: string): string {
  return `'${s.replace(/'/g, `'\\''`)}'`
}

export function openTerminal(cwd: string, args: string[], python: string | null, src: string | null): boolean {
  const base = python ? [python, '-m', 'watchdog'] : ['watchdog']
  const cmd = [...base, ...args].map(shellQuote).join(' ')
  const envPrefix = src ? `PYTHONPATH=${shellQuote(src)} ` : ''
  try {
    if (process.platform === 'darwin') {
      const script = join(tmpdir(), `watchdog-${Date.now()}.command`)
      writeFileSync(script, `#!/bin/bash\ncd ${shellQuote(cwd)}\n${envPrefix}${cmd}\n`)
      chmodSync(script, 0o755)
      spawn('open', ['-a', 'Terminal', script], { detached: true, stdio: 'ignore' }).unref()
      return true
    }
    if (process.platform === 'win32') {
      const winCmd = [...base, ...args].map((a) => `"${a.replace(/"/g, '""')}"`).join(' ')
      spawn('cmd.exe', ['/c', 'start', 'cmd.exe', '/k', `cd /d "${cwd}" && ${winCmd}`], { detached: true, stdio: 'ignore' }).unref()
      return true
    }
    const line = `cd ${shellQuote(cwd)} && ${envPrefix}${cmd}; exec $SHELL`
    for (const term of ['x-terminal-emulator', 'gnome-terminal', 'konsole', 'xfce4-terminal', 'xterm']) {
      const found = (process.env.PATH ?? '').split(':').some((d) => existsSync(join(d, term)))
      if (!found) continue
      const a = term === 'gnome-terminal' ? ['--', 'bash', '-lc', line] : ['-e', `bash -lc ${shellQuote(line)}`]
      spawn(term, a, { detached: true, stdio: 'ignore' }).unref()
      return true
    }
  } catch {
    return false
  }
  return false
}
