import { cpSync, existsSync, readFileSync, statSync } from 'node:fs'
import { join, resolve, sep } from 'node:path'
import type { Plugin } from 'vite'
import { defineConfig, externalizeDepsPlugin } from 'electron-vite'
import react from '@vitejs/plugin-react'

// pdf.js loads some of its pieces at run time: the decoders for JPEG 2000 and JBIG2 images (the
// usual encodings of scanned court and government records; without them a scanned page draws
// blank), colour profiles, character maps for Asian scripts, and the standard fonts a PDF may name
// without embedding. They live in pdfjs-dist; this serves them under /pdfjs/ in development and
// copies them next to the built renderer, where lib/pdf.ts points pdf.js at them.
const PDFJS_ASSETS = ['wasm', 'cmaps', 'standard_fonts', 'iccs']
function pdfjsAssets(): Plugin {
  const root = resolve('node_modules/pdfjs-dist')
  const types: Record<string, string> = { '.wasm': 'application/wasm', '.js': 'text/javascript', '.json': 'application/json' }
  return {
    name: 'pdfjs-assets',
    configureServer(server) {
      server.middlewares.use('/pdfjs', (req, res, next) => {
        const rel = decodeURIComponent((req.url ?? '').split('?')[0]).replace(/^\/+/, '')
        const [dir] = rel.split('/')
        const file = join(root, rel)
        if (!PDFJS_ASSETS.includes(dir) || !file.startsWith(root + sep) || !existsSync(file) || !statSync(file).isFile()) return next()
        const ext = file.slice(file.lastIndexOf('.'))
        res.setHeader('Content-Type', types[ext] ?? 'application/octet-stream')
        res.end(readFileSync(file))
      })
    },
    writeBundle(options) {
      for (const dir of PDFJS_ASSETS) cpSync(join(root, dir), join(options.dir!, 'pdfjs', dir), { recursive: true })
    }
  }
}

export default defineConfig({
  main: {
    plugins: [externalizeDepsPlugin()],
    resolve: { alias: { '@shared': resolve('src/shared') } }
  },
  preload: {
    plugins: [externalizeDepsPlugin()],
    resolve: { alias: { '@shared': resolve('src/shared') } }
  },
  renderer: {
    resolve: {
      alias: {
        '@renderer': resolve('src/renderer/src'),
        '@shared': resolve('src/shared')
      }
    },
    plugins: [react(), pdfjsAssets()],
    worker: { format: 'es' }
  }
})
