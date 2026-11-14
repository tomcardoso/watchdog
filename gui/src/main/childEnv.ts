// The environment the app hands to every program it starts: the Python backend (and through it
// each job, Claude Code and the model libraries), the Claude sign-in, and the engine installer.
// An allowlist, not a blocklist (D409): a variable reaches them only if the operating system, the
// network or Watchdog itself needs it. Everything else in the shell the app was started from is
// left out, so a key, token, endpoint or cloud-provider switch exported for some other program
// can never decide who pays for a model call, or anything else Watchdog does.

/** Names passed through as they are, compared in upper case (Windows names are case-insensitive). */
const NAMES = new Set([
  // Every system: where things are, who is running, the locale and the clock.
  'PATH', 'HOME', 'USER', 'LOGNAME', 'USERNAME', 'SHELL', 'TMPDIR', 'TMP', 'TEMP', 'TZ', 'LANG', 'LANGUAGE',
  // macOS.
  '__CF_USER_TEXT_ENCODING',
  // Linux desktops: the display and the session bus (the keyring is reached over it).
  'DISPLAY', 'WAYLAND_DISPLAY', 'DBUS_SESSION_BUS_ADDRESS',
  // Windows.
  'SYSTEMROOT', 'SYSTEMDRIVE', 'WINDIR', 'COMSPEC', 'PATHEXT', 'APPDATA', 'LOCALAPPDATA', 'PROGRAMDATA',
  'PROGRAMFILES', 'PROGRAMFILES(X86)', 'PROGRAMW6432', 'COMMONPROGRAMFILES', 'COMMONPROGRAMFILES(X86)',
  'COMMONPROGRAMW6432', 'USERPROFILE', 'HOMEDRIVE', 'HOMEPATH', 'PUBLIC', 'ALLUSERSPROFILE', 'COMPUTERNAME',
  'USERDOMAIN', 'OS', 'NUMBER_OF_PROCESSORS', 'PROCESSOR_ARCHITECTURE', 'PROCESSOR_IDENTIFIER',
  // An office network: its proxy and the root certificates it adds for inspecting TLS.
  'HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'NO_PROXY', 'SSL_CERT_FILE', 'SSL_CERT_DIR', 'REQUESTS_CA_BUNDLE',
  'NODE_EXTRA_CA_CERTS',
  // Where local models and OCR data are kept, when the person moved them (D358).
  'HF_HOME', 'HF_HUB_CACHE', 'DOCLING_ARTIFACTS_PATH', 'FASTEMBED_CACHE_PATH', 'TESSDATA_PREFIX'
])

/** Name prefixes passed through: only the two families defined by standards, the locale's parts
 * (POSIX `LC_*`) and the desktop's folders (freedesktop `XDG_*`). Neither holds a credential.
 * Nothing else is matched by prefix, so a look-alike name (`HF_TOKEN`, a `WATCHDOG_` switch set in
 * a shell) never gets through: every `WATCHDOG_` variable the backend reads is set by the app. */
const PREFIXES = ['LC_', 'XDG_']

/** Set on every program the app starts, so the backend knows the app chose its environment and
 * ignores provider keys in it even if one got through (`watchdog.environment`). */
export const APP_MARKER = 'WATCHDOG_APP'

export function allowed(name: string): boolean {
  const up = name.toUpperCase()
  return NAMES.has(up) || PREFIXES.some((p) => up.startsWith(p))
}

/** `source` with only the allowed variables, plus the app's marker. */
export function childEnv(source: NodeJS.ProcessEnv = process.env): NodeJS.ProcessEnv {
  const out: NodeJS.ProcessEnv = {}
  for (const [k, v] of Object.entries(source)) if (v !== undefined && allowed(k)) out[k] = v
  out[APP_MARKER] = '1'
  return out
}
