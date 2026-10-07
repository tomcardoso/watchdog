// Watchdog's mark: an attentive eye inside a shield-like rounded square.

export function LogoMark({ className, style }: { className?: string; style?: React.CSSProperties }) {
  return (
    <svg viewBox="0 0 32 32" fill="none" className={className} style={style} aria-hidden>
      <path d="M3.5 16c3.2-6 7.6-9 12.5-9s9.3 3 12.5 9c-3.2 6-7.6 9-12.5 9s-9.3-3-12.5-9Z" stroke="currentColor" strokeWidth="2.4" strokeLinejoin="round" />
      <circle cx="16" cy="16" r="4.6" fill="currentColor" />
      <circle cx="17.6" cy="14.4" r="1.4" fill="var(--logo-glint, #fff)" />
    </svg>
  )
}
