// Watchdog website: behaviour shared by every page. Theme switch, the search palette (⌘K, as in
// the app), the mobile menu, the "On this page" highlight, and screenshots that open larger.
(() => {
  const root = document.documentElement
  const $ = (s, el = document) => el.querySelector(s)
  const $$ = (s, el = document) => [...el.querySelectorAll(s)]

  // ── Theme ──────────────────────────────────────────────────────────────────────────────────
  const systemDark = matchMedia('(prefers-color-scheme: dark)')
  const isDark = () => root.dataset.theme === 'dark' || (!root.dataset.theme && systemDark.matches)
  $$('.theme-toggle').forEach((b) => b.addEventListener('click', () => {
    const next = isDark() ? 'light' : 'dark'
    root.dataset.theme = next
    try { localStorage.setItem('wd-theme', next) } catch {}
    window.dispatchEvent(new CustomEvent('wd:theme'))
  }))
  systemDark.addEventListener('change', () => window.dispatchEvent(new CustomEvent('wd:theme')))
  window.wdIsDark = isDark

  // ── Header ─────────────────────────────────────────────────────────────────────────────────
  const header = $('.site-header')
  const onScroll = () => header?.classList.toggle('scrolled', scrollY > 8)
  addEventListener('scroll', onScroll, { passive: true })
  onScroll()
  const menu = $('.menu-toggle')
  menu?.addEventListener('click', () => {
    const open = document.body.classList.toggle('menu-open')
    menu.setAttribute('aria-expanded', String(open))
  })
  $$('.site-nav a').forEach((a) => a.addEventListener('click', () => {
    document.body.classList.remove('menu-open')
    menu?.setAttribute('aria-expanded', 'false')
  }))

  // ── Search ─────────────────────────────────────────────────────────────────────────────────
  const dialog = $('.search-dialog')
  const input = $('.search-field input')
  const list = $('.search-results')
  const empty = $('.search-empty')
  let index = null
  let selected = 0
  const esc = (s) => s.replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c])
  const mark = (text, words) => {
    let out = esc(text)
    for (const w of words) out = out.replace(new RegExp(`(${w.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')})`, 'gi'), '<mark>$1</mark>')
    return out
  }
  async function load() {
    if (index) return index
    const res = await fetch('search.json')
    index = (await res.json()).map((e) => ({ ...e, hay: `${e.h} ${e.p} ${e.x}`.toLowerCase(), hl: e.h.toLowerCase() }))
    return index
  }
  function snippet(text, words) {
    const lower = text.toLowerCase()
    const at = Math.min(...words.map((w) => { const i = lower.indexOf(w); return i < 0 ? Infinity : i }))
    if (!isFinite(at) || at < 60) return text.slice(0, 200)
    return '…' + text.slice(at - 50, at + 160)
  }
  async function run() {
    const q = input.value.trim().toLowerCase()
    const words = q.split(/\s+/).filter(Boolean)
    if (!words.length) { list.innerHTML = ''; empty.hidden = true; return }
    const idx = await load()
    const hits = idx
      .filter((e) => words.every((w) => e.hay.includes(w)))
      .map((e) => ({ e, score: words.reduce((s, w) => s + (e.hl.includes(w) ? 6 : 0) + (e.hl.startsWith(w) ? 3 : 0), 0) - e.x.length / 4000 }))
      .sort((a, b) => b.score - a.score)
      .slice(0, 12)
    selected = 0
    list.innerHTML = hits.map(({ e }, i) => `<li><a href="${e.u}" role="option" aria-selected="${i === 0}"><span class="r-page">${esc(e.p)}</span><span class="r-title">${mark(e.h, words)}</span><span class="r-text">${mark(snippet(e.x, words), words)}</span></a></li>`).join('')
    empty.hidden = hits.length > 0
  }
  function select(i) {
    const items = $$('a', list)
    if (!items.length) return
    selected = (i + items.length) % items.length
    items.forEach((a, j) => a.setAttribute('aria-selected', String(j === selected)))
    items[selected].scrollIntoView({ block: 'nearest' })
  }
  function openSearch() {
    if (!dialog) return
    dialog.hidden = false
    input.value = ''
    list.innerHTML = ''
    empty.hidden = true
    input.focus()
    load()
  }
  const closeSearch = () => { if (dialog) dialog.hidden = true }
  $$('.search-open, [data-search]').forEach((b) => b.addEventListener('click', openSearch))
  dialog?.addEventListener('click', (e) => { if (e.target.closest('[data-close]')) closeSearch() })
  list?.addEventListener('click', (e) => { if (e.target.closest('a')) closeSearch() })
  input?.addEventListener('input', run)
  input?.addEventListener('keydown', (e) => {
    if (e.key === 'ArrowDown') { e.preventDefault(); select(selected + 1) }
    else if (e.key === 'ArrowUp') { e.preventDefault(); select(selected - 1) }
    else if (e.key === 'Enter') { const a = $$('a', list)[selected]; if (a) { closeSearch(); location.href = a.href } }
  })
  addEventListener('keydown', (e) => {
    const typing = /INPUT|TEXTAREA/.test(document.activeElement?.tagName ?? '')
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); dialog?.hidden ? openSearch() : closeSearch() }
    else if (e.key === '/' && !typing) { e.preventDefault(); openSearch() }
    else if (e.key === 'Escape') { closeSearch(); closeLightbox() }
  })
  if (!/Mac|iPhone|iPad/.test(navigator.platform)) $$('.kbd-hint').forEach((k) => (k.textContent = 'Ctrl K'))

  // ── On this page ───────────────────────────────────────────────────────────────────────────
  const tocLinks = $$('.doc-toc a')
  if (tocLinks.length && 'IntersectionObserver' in window) {
    const byId = new Map(tocLinks.map((a) => [decodeURIComponent(a.hash.slice(1)), a]))
    const heads = $$('.prose h2[id], .prose h3[id]')
    const visible = new Set()
    const io = new IntersectionObserver((entries) => {
      for (const en of entries) en.isIntersecting ? visible.add(en.target) : visible.delete(en.target)
      let current = heads.find((h) => visible.has(h))
      if (!current) current = [...heads].reverse().find((h) => h.getBoundingClientRect().top < 120)
      tocLinks.forEach((a) => a.classList.toggle('active', byId.get(current?.id) === a))
    }, { rootMargin: '-70px 0px -65% 0px' })
    heads.forEach((h) => io.observe(h))
  }

  // ── Screenshots, larger ────────────────────────────────────────────────────────────────────
  const box = $('.lightbox')
  function closeLightbox() { if (box) box.hidden = true }
  document.addEventListener('click', (e) => {
    const z = e.target.closest('.zoomable')
    if (!z || !box) return
    const img = $$('img', z).find((i) => i.offsetParent !== null) ?? $('img', z)
    $('img', box).src = img.currentSrc || img.src
    $('img', box).alt = img.alt
    box.hidden = false
  })
  box?.addEventListener('click', closeLightbox)
})()
