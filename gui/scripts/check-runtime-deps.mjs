// Fails if the built main or preload code loads a package that is not in package.json's
// `dependencies` (D305). Only those packages ship in the installer: electron-vite leaves them out
// of its bundle, and everything else is bundled or left behind, so a missing one breaks only the
// packaged app, never the dev build or the end-to-end tests. Run after `electron-vite build`.
import { readFileSync, readdirSync } from 'node:fs'
import { builtinModules } from 'node:module'
import { fileURLToPath } from 'node:url'
import { join } from 'node:path'

const gui = fileURLToPath(new URL('..', import.meta.url))
const shipped = new Set(Object.keys(JSON.parse(readFileSync(join(gui, 'package.json'), 'utf8')).dependencies ?? {}))
const builtin = new Set(['electron', ...builtinModules])
const bad = []
for (const dir of ['out/main', 'out/preload']) {
  for (const name of readdirSync(join(gui, dir))) {
    if (!/\.(c|m)?js$/.test(name)) continue
    const code = readFileSync(join(gui, dir, name), 'utf8')
    for (const m of code.matchAll(/(?:require\(|from\s+|import\()\s*["']([^"'./][^"']*)["']/g)) {
      const spec = m[1].replace(/^node:/, '')
      const pkg = spec.startsWith('@') ? spec.split('/').slice(0, 2).join('/') : spec.split('/')[0]
      if (!builtin.has(pkg) && !builtin.has(spec) && !shipped.has(pkg)) bad.push(`${dir}/${name}: ${m[1]}`)
    }
  }
}
if (bad.length) {
  console.error('The built main/preload code loads packages missing from package.json "dependencies"' +
    ' (they would not ship in the installer; move them there):\n' + [...new Set(bad)].join('\n'))
  process.exit(1)
}
console.log('runtime deps: main and preload load only shipped packages')
