// Fails if any renderer file other than styles/tokens.css contains a colour literal (hex, rgb(),
// hsl()). Every colour lives in tokens.css so the palette can change in one place; reference a
// variable there instead, adding a token if none fits.
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join, relative, sep } from 'node:path'
import { fileURLToPath } from 'node:url'

const root = fileURLToPath(new URL('../src/renderer/src', import.meta.url))
const allowed = new Set(['styles/tokens.css'])
const literal = /#[0-9a-fA-F]{3,8}\b(?![\w-])|\b(?:rgba?|hsla?)\(/g
const bad = []

function walk(dir) {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name)
    if (statSync(p).isDirectory()) walk(p)
    else if (/\.(css|tsx?|jsx?)$/.test(name) && !allowed.has(relative(root, p).split(sep).join('/'))) {
      readFileSync(p, 'utf8').split('\n').forEach((line, i) => {
        // Strings like href="#" and '#' anchors are not colours.
        const scrubbed = line.replace(/(["'])#\1/g, '')
        for (const m of scrubbed.matchAll(literal)) bad.push(`${relative(root, p)}:${i + 1}: ${m[0]}  ${line.trim().slice(0, 90)}`)
      })
    }
  }
}
walk(root)
if (bad.length) {
  console.error('Colour literals outside styles/tokens.css — use a token instead:\n' + bad.join('\n'))
  process.exit(1)
}
console.log('colours: every colour comes from tokens.css')
