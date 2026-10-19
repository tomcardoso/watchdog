// Watchdog website: the landing page's motion and interaction. Everything here is decoration on
// top of content that reads fine without it; prefers-reduced-motion stops the loops.
(() => {
  const $ = (s, el = document) => el.querySelector(s)
  const $$ = (s, el = document) => [...el.querySelectorAll(s)]
  const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

  // ── Reveal on scroll ───────────────────────────────────────────────────────────────────────
  const io = new IntersectionObserver((entries) => {
    for (const e of entries) if (e.isIntersecting) { e.target.classList.add('in'); io.unobserve(e.target) }
  }, { rootMargin: '0px 0px -12% 0px', threshold: 0.05 })
  $$('.reveal').forEach((el) => io.observe(el))
  requestAnimationFrame(() => $('.hl')?.classList.add('on'))

  // ── Hero: a page is read, highlighted, and turned into cited facts ─────────────────────────
  const visual = $('.hero-visual')
  if (visual) {
    $$('.pass', visual).forEach((p) => { p.innerHTML = `<span>${p.innerHTML}</span>` })
    const passes = $$('.pass', visual)
    const facts = $$('.fact', visual)
    const flag = $('.flag', visual)
    if (reduced) {
      passes.forEach((p) => p.classList.add('on'))
      facts.forEach((f) => f.classList.add('show'))
      flag.classList.add('show')
    } else {
      let visible = true
      new IntersectionObserver(([e]) => { visible = e.isIntersecting }).observe(visual)
      ;(async function loop() {
        await sleep(900)
        for (;;) {
          while (!visible || document.hidden) await sleep(400)
          visual.classList.remove('fading')
          for (let i = 0; i < passes.length; i++) {
            passes[i].classList.add('on')
            await sleep(1000)
            facts[i].classList.add('show')
            await sleep(1500)
          }
          flag.classList.add('show')
          await sleep(5200)
          visual.classList.add('fading')
          await sleep(700)
          passes.forEach((p) => p.classList.remove('on'))
          facts.forEach((f) => f.classList.remove('show'))
          flag.classList.remove('show')
          await sleep(900)
        }
      })()
    }
  }

  // ── Tour: tabs that advance on their own until someone picks one ───────────────────────────
  const tabs = $$('.tour-tabs [role=tab]')
  if (tabs.length) {
    const DWELL = 6500
    let current = 0
    let auto = !reduced
    let start = performance.now()
    let inView = false
    const show = (i) => {
      current = (i + tabs.length) % tabs.length
      const key = tabs[current].dataset.shot
      tabs.forEach((t, j) => { t.setAttribute('aria-selected', String(j === current)); t.style.setProperty('--p', '0') })
      $$('.screen').forEach((s) => s.classList.toggle('active', s.dataset.shot === key))
      $$('.caption').forEach((c) => c.classList.toggle('active', c.dataset.shot === key))
      // Load the next screen's images ahead of time.
      $$(`.screen[data-shot="${tabs[(current + 1) % tabs.length].dataset.shot}"] img`).forEach((img) => { img.loading = 'eager' })
      start = performance.now()
    }
    tabs.forEach((t, i) => t.addEventListener('click', () => { auto = false; show(i) }))
    $('.tour-tabs').addEventListener('keydown', (e) => {
      if (e.key !== 'ArrowRight' && e.key !== 'ArrowLeft') return
      auto = false
      show(current + (e.key === 'ArrowRight' ? 1 : -1))
      tabs[current].focus()
    })
    new IntersectionObserver(([e]) => { inView = e.isIntersecting; start = performance.now() }).observe($('.tour-stage'))
    const tick = (now) => {
      if (auto && inView && !document.hidden) {
        const p = Math.min(1, (now - start) / DWELL)
        tabs[current].style.setProperty('--p', p.toFixed(3))
        if (p >= 1) show(current + 1)
      } else {
        start = now - (parseFloat(tabs[current].style.getPropertyValue('--p')) || 0) * DWELL
        if (!auto) tabs[current].style.setProperty('--p', '1')
      }
      requestAnimationFrame(tick)
    }
    requestAnimationFrame(tick)
  }

  // ── Fact checking: mark the fact as a reporter would ───────────────────────────────────────
  const card = $('.fact-card')
  if (card) {
    const words = { verified: 'Verified', disputed: 'Disputed', unverifiable: "Can't verify" }
    const stamp = $('.stamp', card)
    $$('.marks button', card).forEach((b) => b.addEventListener('click', () => {
      const on = card.dataset.state !== b.dataset.mark
      card.dataset.state = on ? b.dataset.mark : ''
      $$('.marks button', card).forEach((x) => x.setAttribute('aria-pressed', String(on && x === b)))
      stamp.textContent = on ? words[b.dataset.mark] : ''
    }))
  }

  // ── Skills: a seamless loop needs each row twice ───────────────────────────────────────────
  $$('.marquee-row').forEach((row) => {
    if (reduced) return
    row.append(...$$('span', row).map((s) => { const c = s.cloneNode(true); c.setAttribute('aria-hidden', 'true'); return c }))
  })

  // ── Download: point at this computer's installer ───────────────────────────────────────────
  const ua = navigator.userAgent
  const os = /Windows/.test(ua) ? 'windows' : /Mac OS X|Macintosh/.test(ua) && !/iPhone|iPad/.test(ua) ? 'mac' : /Linux/.test(ua) && !/Android/.test(ua) ? 'linux' : null
  const primary = $('.download-primary')
  if (os && primary) {
    const names = { mac: 'Download for Mac', windows: 'Download for Windows', linux: 'Download for Linux' }
    primary.href = primary.dataset[os]
    $('.download-label', primary).textContent = names[os]
    const note = $('.download-note')
    if (note) note.innerHTML = os === 'mac'
      ? 'For Macs with Apple silicon. <a href="#download">Intel, Windows and Linux</a>.'
      : `Also for <a href="#download">${os === 'windows' ? 'Mac and Linux' : 'Mac and Windows'}</a>.`
    $(`.platform[data-os="${os}"]`)?.classList.add('detected')
  }

  // ── Network: the sample investigation's real graph, laid out by a small force simulation ───
  const canvas = $('.net-canvas')
  if (canvas) network(canvas)

  async function network(canvas) {
    const stage = canvas.parentElement
    const tip = $('.net-tip', stage)
    const ctx = canvas.getContext('2d')
    let data
    try { data = await (await fetch('data/graph.json')).json() } catch { return }
    const TYPES = { person: 'Person', organization: 'Organization', 'public-body': 'Public body', place: 'Place', asset: 'Asset', proceeding: 'Proceeding' }
    const nodes = data.nodes.map((n, i) => {
      const a = (i / data.nodes.length) * Math.PI * 2
      return { ...n, r: 4.5 + Math.sqrt(n.docs) * 2.6, x: Math.cos(a) * 160 + (Math.random() - 0.5) * 40, y: Math.sin(a) * 160 + (Math.random() - 0.5) * 40, vx: 0, vy: 0, links: [] }
    })
    const byId = new Map(nodes.map((n) => [n.id, n]))
    const links = data.links.map((l) => ({ s: byId.get(l.source), t: byId.get(l.target), labels: l.labels })).filter((l) => l.s && l.t)
    for (const l of links) { l.s.links.push(l); l.t.links.push(l) }
    $('.net-count', stage).textContent = `${nodes.length} entities · ${links.length} connections`

    let W = 0, H = 0, dpr = 1, colours = {}
    let hover = null, drag = null, scale = 1
    const readColours = () => {
      const cs = getComputedStyle(document.documentElement)
      const v = (k) => cs.getPropertyValue(k).trim()
      colours = { edge: v('--border-strong'), text: v('--text'), text2: v('--text-2'), surface: v('--surface'), hl: v('--marker') }
      for (const t of Object.keys(TYPES)) colours[t] = v(`--${t}`)
    }
    const resize = () => {
      dpr = Math.min(2, devicePixelRatio || 1)
      W = stage.clientWidth
      H = stage.clientHeight
      canvas.width = W * dpr
      canvas.height = H * dpr
      draw()
    }
    readColours()
    addEventListener('wd:theme', () => { readColours(); draw() })
    new ResizeObserver(resize).observe(stage)

    // Physics: pairwise repulsion, springs along links, a pull to the centre, damping.
    let alpha = 1
    const step = () => {
      const n = nodes.length
      for (let i = 0; i < n; i++) {
        const a = nodes[i]
        for (let j = i + 1; j < n; j++) {
          const b = nodes[j]
          let dx = b.x - a.x, dy = b.y - a.y
          let d2 = dx * dx + dy * dy || 0.01
          if (d2 > 160000) continue
          const f = (2600 / d2) * alpha
          const d = Math.sqrt(d2)
          dx /= d; dy /= d
          a.vx -= dx * f; a.vy -= dy * f
          b.vx += dx * f; b.vy += dy * f
        }
      }
      for (const l of links) {
        const dx = l.t.x - l.s.x, dy = l.t.y - l.s.y
        const d = Math.sqrt(dx * dx + dy * dy) || 1
        const target = 58 + l.s.r + l.t.r
        const f = ((d - target) / d) * 0.05 * alpha
        l.s.vx += dx * f; l.s.vy += dy * f
        l.t.vx -= dx * f; l.t.vy -= dy * f
      }
      for (const a of nodes) {
        a.vx -= a.x * 0.004 * alpha
        a.vy -= a.y * 0.007 * alpha
        if (a === drag) { a.vx = a.vy = 0; continue }
        a.vx *= 0.82; a.vy *= 0.82
        a.x += a.vx; a.y += a.vy
      }
      alpha = Math.max(0.02, alpha * 0.992)
    }
    for (let i = 0; i < 420; i++) step()

    const fit = () => {
      let maxX = 1, maxY = 1
      for (const a of nodes) { maxX = Math.max(maxX, Math.abs(a.x) + a.r + 70); maxY = Math.max(maxY, Math.abs(a.y) + a.r + 24) }
      scale = Math.min(1.35, (W / 2) / maxX, (H / 2) / maxY)
    }
    const toScreen = (a) => [W / 2 + a.x * scale, H / 2 + a.y * scale]

    function draw() {
      if (!W) return
      fit()
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
      ctx.clearRect(0, 0, W, H)
      const near = hover ? new Set([hover, ...hover.links.map((l) => (l.s === hover ? l.t : l.s))]) : null
      for (const l of links) {
        const on = hover && (l.s === hover || l.t === hover)
        const [x1, y1] = toScreen(l.s), [x2, y2] = toScreen(l.t)
        ctx.globalAlpha = hover ? (on ? 1 : 0.12) : 0.7
        ctx.strokeStyle = on ? colours.text2 : colours.edge
        ctx.lineWidth = on ? 1.8 : 1.1
        ctx.beginPath(); ctx.moveTo(x1, y1); ctx.lineTo(x2, y2); ctx.stroke()
      }
      for (const a of nodes) {
        const [x, y] = toScreen(a)
        const r = a.r * Math.max(0.8, scale)
        ctx.globalAlpha = near && !near.has(a) ? 0.18 : 1
        if (a === hover) { ctx.fillStyle = colours.hl; ctx.beginPath(); ctx.arc(x, y, r + 6, 0, Math.PI * 2); ctx.fill() }
        ctx.fillStyle = colours[a.type] || colours.text2
        ctx.strokeStyle = colours.surface
        ctx.lineWidth = 2
        ctx.beginPath(); ctx.arc(x, y, r, 0, Math.PI * 2); ctx.fill(); ctx.stroke()
      }
      ctx.font = '500 12px "Inter Variable", system-ui, sans-serif'
      ctx.textAlign = 'center'
      ctx.textBaseline = 'top'
      for (const a of nodes) {
        const show = near ? near.has(a) : a.docs >= 6
        if (!show) continue
        const [x, y] = toScreen(a)
        const r = a.r * Math.max(0.8, scale)
        const label = a.name.length > 30 ? a.name.slice(0, 29) + '…' : a.name
        ctx.globalAlpha = 1
        ctx.lineWidth = 4
        ctx.strokeStyle = colours.surface
        ctx.strokeText(label, x, y + r + 4)
        ctx.fillStyle = a === hover ? colours.text : colours.text2
        ctx.fillText(label, x, y + r + 4)
      }
      ctx.globalAlpha = 1
    }

    const pick = (mx, my) => {
      let best = null, bd = Infinity
      for (const a of nodes) {
        const [x, y] = toScreen(a)
        const d = Math.hypot(x - mx, y - my)
        if (d < a.r * Math.max(0.8, scale) + 8 && d < bd) { best = a; bd = d }
      }
      return best
    }
    const showTip = (a, mx, my) => {
      if (!a) { tip.hidden = true; return }
      const rels = a.links.slice(0, 6).map((l) => {
        const other = l.s === a ? l.t : l.s
        const label = l.labels[0]
        return `<li>${l.s === a ? `${esc(label)} <b style="display:inline">${esc(other.name)}</b>` : `<b style="display:inline">${esc(other.name)}</b> · ${esc(label.toLowerCase())}`}</li>`
      }).join('')
      const more = a.links.length > 6 ? `<li>and ${a.links.length - 6} more</li>` : ''
      tip.innerHTML = `<b>${esc(a.name)}</b><span class="t-type">${TYPES[a.type] ?? a.type} · named in ${a.docs} document${a.docs === 1 ? '' : 's'}</span><ul>${rels}${more}</ul>`
      tip.hidden = false
      const tw = tip.offsetWidth, th = tip.offsetHeight
      tip.style.left = `${Math.min(W - tw - 10, Math.max(10, mx + 16))}px`
      tip.style.top = `${Math.min(H - th - 10, Math.max(10, my + 16))}px`
    }
    const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c])
    const pos = (e) => { const r = canvas.getBoundingClientRect(); return [e.clientX - r.left, e.clientY - r.top] }

    canvas.addEventListener('pointermove', (e) => {
      const [mx, my] = pos(e)
      if (drag) {
        drag.x = (mx - W / 2) / scale
        drag.y = (my - H / 2) / scale
        alpha = Math.max(alpha, 0.35)
        wake()
        showTip(drag, mx, my)
        return
      }
      const a = pick(mx, my)
      if (a !== hover) { hover = a; draw() }
      showTip(a, mx, my)
    })
    canvas.addEventListener('pointerleave', () => { if (!drag) { hover = null; tip.hidden = true; draw() } })
    canvas.addEventListener('pointerdown', (e) => {
      const [mx, my] = pos(e)
      const a = pick(mx, my)
      if (!a) return
      drag = hover = a
      canvas.setPointerCapture(e.pointerId)
      canvas.classList.add('dragging')
    })
    const release = () => { drag = null; canvas.classList.remove('dragging') }
    canvas.addEventListener('pointerup', release)
    canvas.addEventListener('pointercancel', release)

    // Animate only while the graph is on screen and still settling.
    let running = false, onScreen = false
    const frame = () => {
      if (!onScreen || (alpha <= 0.021 && !drag)) { running = false; return }
      step(); draw()
      requestAnimationFrame(frame)
    }
    const wake = () => { if (!running && onScreen && !reduced) { running = true; requestAnimationFrame(frame) } }
    new IntersectionObserver(([e]) => { onScreen = e.isIntersecting; if (onScreen) { alpha = Math.max(alpha, 0.3); wake() } }).observe(stage)
    resize()
  }
})()
