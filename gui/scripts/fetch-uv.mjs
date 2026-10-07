// Downloads the pinned `uv` release (https://github.com/astral-sh/uv) for the platforms the app is
// being built for, verifies its sha256, and unpacks the binary into resources/bin/<os>-<arch>/.
// electron-builder ships that folder as extraResources (see electron-builder.yml), and the app's
// installer uses it to build the managed Python engine on a user's computer (src/main/engine.ts).
//
//   node scripts/fetch-uv.mjs                         # this computer's platform and architecture
//   node scripts/fetch-uv.mjs --target mac --arch arm64,x64
//   node scripts/fetch-uv.mjs --all                   # every supported target
//   node scripts/fetch-uv.mjs --force                 # download again even when present
//
// <os> is electron-builder's `${os}` (mac, linux, win), so the config can pick the folder by macro.
// Two checks guard the download: the digest pinned in this file (changing the version means
// changing the digests, on purpose) and the .sha256 file GitHub publishes beside the archive.

import { spawnSync } from 'node:child_process'
import { createHash } from 'node:crypto'
import { chmodSync, copyFileSync, existsSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

export const UV_VERSION = '0.11.32'

const TARGETS = {
  'mac-arm64': { triple: 'aarch64-apple-darwin', ext: 'tar.gz', sha256: 'ed336d0ba49db8ef89b2b41fffa372ce63bd032f22a56f001c265891aec32829' },
  'mac-x64': { triple: 'x86_64-apple-darwin', ext: 'tar.gz', sha256: '77f5ca26c0de20e992a3677a174fe1121ee25c36f9b1434a863f75bf077a05eb' },
  'linux-x64': { triple: 'x86_64-unknown-linux-gnu', ext: 'tar.gz', sha256: 'aab924fd522efd06f1c5f3b93a243864fc453132c94b2dc49f1371b528a4b967' },
  'linux-arm64': { triple: 'aarch64-unknown-linux-gnu', ext: 'tar.gz', sha256: '4d4fa08d95b06642e5800df6a22bd71455f23f988269e18da2847971d8c0bf31' },
  'win-x64': { triple: 'x86_64-pc-windows-msvc', ext: 'zip', sha256: 'acfde570451cfdb8689fa159a138ee805ba4e241c466432750302c86254b0984' },
  'win-arm64': { triple: 'aarch64-pc-windows-msvc', ext: 'zip', sha256: 'a7427ea0440bb826b6716d1837ff3d173b8e7d496cb09ee8f456b4e023a2fdcd' }
}

const here = dirname(fileURLToPath(import.meta.url))
const binRoot = resolve(here, '..', 'resources', 'bin')

const hostOs = { darwin: 'mac', linux: 'linux', win32: 'win' }[process.platform]
const hostArch = { x64: 'x64', arm64: 'arm64' }[process.arch]

function wanted(args) {
  const opt = (name) => {
    const i = args.indexOf(`--${name}`)
    return i >= 0 ? args[i + 1] : undefined
  }
  if (args.includes('--all')) return Object.keys(TARGETS)
  const os = opt('target') ?? hostOs
  const arches = (opt('arch') ?? hostArch ?? '').split(',').filter(Boolean)
  const keys = arches.map((a) => `${os}-${a}`)
  for (const k of keys) if (!TARGETS[k]) throw new Error(`No uv build for ${k}. Supported: ${Object.keys(TARGETS).join(', ')}`)
  return keys
}

async function download(url) {
  const res = await fetch(url, { redirect: 'follow' })
  if (!res.ok) throw new Error(`${url}: HTTP ${res.status}`)
  return Buffer.from(await res.arrayBuffer())
}

function extract(archive, ext, into) {
  // bsdtar (macOS, Windows 10+) reads both formats; on Linux a zip needs unzip.
  const tries = ext === 'zip' && process.platform === 'linux' ? [['unzip', ['-q', archive, '-d', into]]] : [['tar', ['-xf', archive, '-C', into]]]
  for (const [cmd, a] of tries) {
    const r = spawnSync(cmd, a, { stdio: 'inherit' })
    if (r.status === 0) return
  }
  throw new Error(`Could not unpack ${archive}`)
}

function findBinary(dir, name) {
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const p = join(dir, entry.name)
    if (entry.isDirectory()) {
      const found = findBinary(p, name)
      if (found) return found
    } else if (entry.name === name) return p
  }
  return null
}

async function fetchTarget(key, force) {
  const t = TARGETS[key]
  const exe = key.startsWith('win') ? 'uv.exe' : 'uv'
  const dest = join(binRoot, key, exe)
  const stamp = join(binRoot, key, 'uv.version')
  if (!force && existsSync(dest) && existsSync(stamp) && readFileSync(stamp, 'utf8').trim() === UV_VERSION) {
    console.log(`uv ${UV_VERSION} for ${key}: already present`)
    return
  }
  const name = `uv-${t.triple}.${t.ext}`
  const base = `https://github.com/astral-sh/uv/releases/download/${UV_VERSION}/${name}`
  console.log(`uv ${UV_VERSION} for ${key}: downloading ${name}`)
  const data = await download(base)
  const digest = createHash('sha256').update(data).digest('hex')
  if (digest !== t.sha256) throw new Error(`${name}: sha256 ${digest} does not match the pinned ${t.sha256}`)
  const published = (await download(base + '.sha256')).toString('utf8').trim().split(/\s+/)[0]
  if (published !== digest) throw new Error(`${name}: sha256 ${digest} does not match the published ${published}`)

  const work = mkdtempSync(join(tmpdir(), 'wd-uv-'))
  try {
    const archive = join(work, name)
    writeFileSync(archive, data)
    const out = join(work, 'out')
    mkdirSync(out)
    extract(archive, t.ext, out)
    const found = findBinary(out, exe)
    if (!found) throw new Error(`${exe} not found inside ${name}`)
    mkdirSync(dirname(dest), { recursive: true })
    copyFileSync(found, dest)
    if (!exe.endsWith('.exe')) chmodSync(dest, 0o755)
    writeFileSync(stamp, UV_VERSION + '\n')
  } finally {
    rmSync(work, { recursive: true, force: true })
  }
  console.log(`  verified sha256 ${digest.slice(0, 16)}… and wrote ${dest}`)
}

export async function fetchUv(args = process.argv.slice(2)) {
  // Node's fetch ignores HTTPS_PROXY unless asked; re-run once with that switched on.
  if ((process.env.HTTPS_PROXY || process.env.https_proxy) && !process.env.NODE_USE_ENV_PROXY && !process.env.WD_FETCH_UV_CHILD) {
    const r = spawnSync(process.execPath, [fileURLToPath(import.meta.url), ...args], {
      stdio: 'inherit',
      env: { ...process.env, NODE_USE_ENV_PROXY: '1', WD_FETCH_UV_CHILD: '1' }
    })
    if (r.status !== 0) process.exit(r.status ?? 1)
    return
  }
  for (const key of wanted(args)) await fetchTarget(key, args.includes('--force'))
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  fetchUv().catch((e) => {
    console.error(e.message)
    process.exit(1)
  })
}
