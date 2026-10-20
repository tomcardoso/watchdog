// Renders resources/icon.png (1024×1024): the Jack Russell mark (also in src/renderer/src/components/
// Logo.tsx, docs/images/logo.svg and site/src/favicon.svg) on a white tile. The dog is placed by eye,
// not by its bounding box, which the tail and nose pull off centre. electron-builder derives the
// .icns/.ico.
import { chromium } from '@playwright/test'
import { resolve } from 'node:path'

const svg = `
<svg xmlns="http://www.w3.org/2000/svg" width="1024" height="1024" viewBox="0 0 1024 1024">
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#ffffff"/><stop offset="1" stop-color="#ececef"/>
    </linearGradient>
  </defs>
  <rect x="100" y="100" width="824" height="824" rx="190" fill="url(#bg)"/>
  <rect x="101.5" y="101.5" width="821" height="821" rx="188.5" fill="none" stroke="#141416" stroke-opacity="0.12" stroke-width="3"/>
  <g transform="translate(100 100) scale(4.12) translate(-18 -5)">
    <path d="M68 158 C57 154 48 145 44 131 C43 126 49 124 51 129 C55 140 63 146 71 148 Z" fill="#ffffff" stroke="#141416" stroke-width="4" stroke-linejoin="round"/>
    <path d="M126 38 C138 36 146 42 148 48 L168 54 C174 56 174 63 168 65 L152 70 C148 76 144 80 142 86 C150 98 152 110 150 124 C148 138 148 152 148 166 L160 167 C163 169 162 173 159 173 L136 173 C134 160 134 148 132 140 C124 148 116 156 114 166 L126 167 C129 169 128 173 125 173 L84 173 C70 173 64 162 68 148 C72 130 80 116 90 104 C100 92 106 80 110 64 C112 50 116 40 126 38 Z" fill="#ffffff"/>
    <clipPath id="body"><path d="M126 38 C138 36 146 42 148 48 L168 54 C174 56 174 63 168 65 L152 70 C148 76 144 80 142 86 C150 98 152 110 150 124 C148 138 148 152 148 166 L160 167 C163 169 162 173 159 173 L136 173 C134 160 134 148 132 140 C124 148 116 156 114 166 L126 167 C129 169 128 173 125 173 L84 173 C70 173 64 162 68 148 C72 130 80 116 90 104 C100 92 106 80 110 64 C112 50 116 40 126 38 Z"/></clipPath>
    <g clip-path="url(#body)"><path d="M104 60 C114 44 136 36 150 46 L148 60 C138 58 128 62 118 72 Z" fill="#c98a4b"/>
    <path d="M78 128 C88 112 104 104 116 106 C118 122 106 136 90 140 C82 138 77 134 78 128 Z" fill="#c98a4b"/></g>
    <path d="M126 38 C138 36 146 42 148 48 L168 54 C174 56 174 63 168 65 L152 70 C148 76 144 80 142 86 C150 98 152 110 150 124 C148 138 148 152 148 166 L160 167 C163 169 162 173 159 173 L136 173 C134 160 134 148 132 140 C124 148 116 156 114 166 L126 167 C129 169 128 173 125 173 L84 173 C70 173 64 162 68 148 C72 130 80 116 90 104 C100 92 106 80 110 64 C112 50 116 40 126 38 Z" fill="none" stroke="#141416" stroke-width="4" stroke-linejoin="round"/>
    <path d="M114 166 C110 152 102 142 90 138" fill="none" stroke="#141416" stroke-width="3" stroke-linecap="round"/>
    <path d="M116 44 C121 34 134 33 138 39 C134 45 129 51 122 55 Z" fill="#c98a4b" stroke="#141416" stroke-width="3.5" stroke-linejoin="round"/>
    <circle cx="144" cy="52" r="3.8" fill="#141416"/>
    <circle cx="169" cy="59" r="4.4" fill="#141416"/>
  </g>
</svg>`
const browser = await chromium.launch(process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {})
const page = await browser.newPage({ viewport: { width: 1024, height: 1024 } })
await page.setContent(`<html><body style="margin:0;background:transparent">${svg}</body></html>`)
await page.screenshot({ path: resolve('resources/icon.png'), omitBackground: true })
await browser.close()
console.log('wrote resources/icon.png')
