// Watchdog's mark: a Jack Russell on watch. The same drawing is in docs/images/logo.svg,
// site/src/favicon.svg and gui/scripts/make-icon.mjs (the app icon); change them together.
import { useId } from 'react'

const BODY =
  'M126 38 C138 36 146 42 148 48 L168 54 C174 56 174 63 168 65 L152 70 C148 76 144 80 142 86 C150 98 152 110 150 124 C148 138 148 152 148 166 L160 167 C163 169 162 173 159 173 L136 173 C134 160 134 148 132 140 C124 148 116 156 114 166 L126 167 C129 169 128 173 125 173 L84 173 C70 173 64 162 68 148 C72 130 80 116 90 104 C100 92 106 80 110 64 C112 50 116 40 126 38 Z'
const INK = 'var(--logo-ink)'
const COAT = 'var(--logo-coat)'
const TAN = 'var(--logo-tan)'
const HALO = 'var(--logo-halo)'
const TAIL = 'M68 158 C57 154 48 145 44 131 C43 126 49 124 51 129 C55 140 63 146 71 148 Z'

// Hovering the mark, or a wordmark holding it (.logo-wag), wags its tail (.logo-tail in styles/base.css).
// Line weights match docs/images/logo.svg, the website's dog.
export function LogoMark({ className, style }: { className?: string; style?: React.CSSProperties }) {
  const clip = `wd-${useId().replace(/[^a-zA-Z0-9]/g, '')}`
  return (
    <svg viewBox="40 31 137 146" className={className ? `logo-mark ${className}` : 'logo-mark'} style={style} aria-hidden>
      {/* The halo: a light edge round the outline, like a sticker's, drawn only in dark mode (tokens.css) */}
      <path className="logo-tail" d={TAIL} fill={HALO} stroke={HALO} strokeWidth="14.5" strokeLinejoin="round" />
      <path d={BODY} fill={HALO} stroke={HALO} strokeWidth="14.5" strokeLinejoin="round" />
      <path className="logo-tail" d={TAIL} fill={COAT} stroke={INK} strokeWidth="4.5" strokeLinejoin="round" />
      <path d={BODY} fill={COAT} />
      <clipPath id={clip}>
        <path d={BODY} />
      </clipPath>
      <g clipPath={`url(#${clip})`}>
        <path d="M104 60 C114 44 136 36 150 46 L148 60 C138 58 128 62 118 72 Z" fill={TAN} />
        <path d="M78 128 C88 112 104 104 116 106 C118 122 106 136 90 140 C82 138 77 134 78 128 Z" fill={TAN} />
      </g>
      <path d={BODY} fill="none" stroke={INK} strokeWidth="4.5" strokeLinejoin="round" />
      <path d="M114 166 C110 152 102 142 90 138" fill="none" stroke={INK} strokeWidth="3" strokeLinecap="round" />
      <path d="M116 44 C121 34 134 33 138 39 C134 45 129 51 122 55 Z" fill={TAN} stroke={INK} strokeWidth="3.5" strokeLinejoin="round" />
      <circle cx="144" cy="52" r="3.8" fill={INK} />
      <circle cx="169" cy="59" r="4.4" fill={INK} />
    </svg>
  )
}
