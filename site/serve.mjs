// A static server for dist/, for previewing the site locally: http://localhost:4321
import { createServer } from 'node:http'
import { existsSync, readFileSync, statSync } from 'node:fs'
import { extname, join, normalize } from 'node:path'
import { fileURLToPath } from 'node:url'

const DIST = fileURLToPath(new URL('./dist', import.meta.url))
const TYPES = { '.html': 'text/html; charset=utf-8', '.css': 'text/css', '.js': 'text/javascript', '.json': 'application/json', '.svg': 'image/svg+xml', '.png': 'image/png', '.woff2': 'font/woff2', '.xml': 'application/xml', '.txt': 'text/plain' }
const port = Number(process.env.PORT ?? 4321)

createServer((req, res) => {
  let path = normalize(decodeURIComponent(new URL(req.url, 'http://x').pathname)).replace(/^(\.\.[/\\])+/, '')
  let file = join(DIST, path)
  if (existsSync(file) && statSync(file).isDirectory()) file = join(file, 'index.html')
  if (!existsSync(file) && existsSync(file + '.html')) file += '.html'
  if (!existsSync(file)) { res.writeHead(404, { 'Content-Type': TYPES['.html'] }); return res.end(readFileSync(join(DIST, '404.html'))) }
  res.writeHead(200, { 'Content-Type': TYPES[extname(file)] ?? 'application/octet-stream' })
  res.end(readFileSync(file))
}).listen(port, () => console.log(`Serving dist/ on http://localhost:${port}`))
