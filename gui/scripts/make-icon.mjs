// Renders resources/icon.png (1024×1024) from the logo, in the Highlighter palette (the values
// mirror --brand-from/--brand-to and --marker in src/renderer/src/styles/tokens.css). electron-builder derives the .icns/.ico.
import { chromium } from '@playwright/test'
import { resolve } from 'node:path'

const svg = `
<svg xmlns="http://www.w3.org/2000/svg" width="1024" height="1024" viewBox="0 0 1024 1024">
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="#36363c"/><stop offset="1" stop-color="#0c0c0e"/>
    </linearGradient>
    <radialGradient id="sheen" cx="0.3" cy="0.15" r="0.9">
      <stop offset="0" stop-color="#fff" stop-opacity="0.12"/><stop offset="0.6" stop-color="#fff" stop-opacity="0"/>
    </radialGradient>
  </defs>
  <rect x="100" y="100" width="824" height="824" rx="190" fill="url(#bg)"/>
  <rect x="100" y="100" width="824" height="824" rx="190" fill="url(#sheen)"/>
  <rect x="101" y="101" width="822" height="822" rx="189" fill="none" stroke="#fff" stroke-opacity="0.1" stroke-width="3"/>
  <g transform="translate(512 512) scale(17) translate(-16 -16)">
    <path d="M3.5 16c3.2-6 7.6-9 12.5-9s9.3 3 12.5 9c-3.2 6-7.6 9-12.5 9s-9.3-3-12.5-9Z" fill="none" stroke="#facc15" stroke-width="2.3" stroke-linejoin="round"/>
    <circle cx="16" cy="16" r="4.6" fill="#facc15"/>
    <circle cx="17.6" cy="14.4" r="1.4" fill="#141416"/>
  </g>
</svg>`
const browser = await chromium.launch(process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {})
const page = await browser.newPage({ viewport: { width: 1024, height: 1024 } })
await page.setContent(`<html><body style="margin:0;background:transparent">${svg}</body></html>`)
await page.screenshot({ path: resolve('resources/icon.png'), omitBackground: true })
await browser.close()
console.log('wrote resources/icon.png')
