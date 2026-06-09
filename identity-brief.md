# Visual identity brief — Watchdog

## What this is

Watchdog is a command-line tool for investigative journalists. It reads large collections of public records — court filings, corporate registrations, land titles, FOI responses — extracts entities and relationships, and stores them as a linked knowledge graph in an Obsidian vault. It runs entirely on the journalist's computer.

The name carries deliberate meaning: the press watchdog tradition (holding power to account) and the literal sense of a dog on watch — alert, loyal, guarding.

## Audience and tone

Users are investigative reporters and data journalists. Technically competent, serious professionals. The tone across all Watchdog's surfaces is precise, understated, and trustworthy. Not playful. Not startup-y. Think wire service, not product launch.

The identity should feel like a tool a veteran journalist would recommend to a colleague — not a consumer app, not a developer toy.

## Three delivery contexts

The identity must work across all three:

### 1. Terminal / CLI
Text-only environment. Black or dark background. Monospace font. ANSI colours available (bold, dim, colour) but the mark itself must read in pure monochrome. This is where the tool lives most of the time — the CLI banner is what journalists see every day.

The ideal CLI expression is an ASCII art mark: a compact, recognisable rendering of the logo in text characters that works at roughly 40–60 columns wide. Think of how tools like neofetch, htop, or the classic Linux boot screens use ASCII art — iconic and immediately recognisable even at low resolution.

### 2. GitHub README / repository
SVG mark rendered on GitHub's dark and light themes. Needs to work as a small inline badge (16–32px) and as a larger header image. GitHub renders SVG but not all SVG features — keep it simple.

### 3. Documentation website
Full expression: mark + wordmark, colour palette, potentially an animated or higher-fidelity version of the mark. The docs site uses a dark or neutral palette.

## Primary direction: the dog mark

The preferred direction is a dog mark — specifically one that captures the *watchdog* concept: alert, forward-facing, on guard. Not a cute or cartoon dog. Not a pet. A working dog — the kind that watches.

Key qualities to aim for in the mark:
- **Alert posture** — ears up, eyes forward, attention fixed. The dog is watching something.
- **Reducible** — the mark should collapse to a simple, recognisable silhouette. At small sizes (favicon, badge) it should still read as a dog. In ASCII art it should still read as a dog.
- **Serious** — clean lines, no decorative flourishes. A dog you'd trust with something important.

The magnifying glass element (currently in the 🔍🐕 emoji pair) is optional — if it complicates the ASCII version, drop it. The dog alone carries the meaning.

## Current ASCII dog (the starting point)

The setup command already shows this ASCII dog on completion:

```
      / \__
     (    @\___
     /         O
    /   (_____/
   /_____/   U
```

This is the aesthetic baseline — compact, readable in monochrome, clearly a sitting dog. The new identity's ASCII version should feel consistent with this or be a deliberate evolution of it. It doesn't need to be preserved exactly, but any replacement should pass the same test: recognisable as a dog, clean at terminal width, no colour required.

## The ASCII art constraint

This is the hardest constraint and should drive the mark design. The ASCII version is not an afterthought — it's the primary daily expression of the identity for most users.

A good ASCII dog is:
- Recognisable as a dog from the silhouette, not from detail
- Compact: roughly 5–8 lines tall, 30–50 characters wide
- Reads clearly in monochrome (no relying on colour to distinguish parts)
- Works at 80-column terminal width alongside the wordmark "Watchdog"

Suggested approach: design the vector mark and the ASCII version together, treating the ASCII as a constraint that shapes the vector rather than a derivative. The Tux penguin (Linux mascot) is a useful reference — it works as both a vector illustration and an ASCII rendering because the silhouette is simple and distinctive.

## Colour palette

The palette should work in dark contexts first (terminal, dark-mode docs, dark GitHub theme) and extend to light contexts second.

Suggestions to explore:
- A dark base (near-black) with one strong accent colour — amber, burnt orange, or deep teal read as investigative/serious without being corporate-blue
- Avoid: bright primary colours, gradients, anything that reads as "tech startup"
- The CLI will use ANSI colour; the web will use hex — they don't need to match exactly but should feel related

## Typography (for wordmark and docs)

- Monospace or semi-monospace for the wordmark — consistent with the CLI context
- Or a strong, condensed sans-serif that reads well at small sizes
- The wordmark is always "Watchdog" — sentence case, not ALL CAPS

## Deliverables needed

1. **Vector mark** — the dog logo in SVG, suitable for docs and README header. Provide dark and light variants.
2. **ASCII art version** — the mark rendered in text characters for the CLI banner. Should work in monochrome.
3. **Favicon / small mark** — 32×32 equivalent, suitable for browser tabs and GitHub avatar
4. **Colour palette** — 3–5 colours with hex values, with guidance on dark vs. light use
5. **Wordmark** — the logotype ("Watchdog") in the chosen typeface, in SVG

## What to avoid

- Cute or cartoon aesthetics — this is a professional tool
- Magnifying glass clichés (already everywhere in "investigation" branding)
- Generic "data" imagery: nodes, networks, circuit boards
- Anything that requires colour to be legible at the CLI
- Complex gradients or effects that won't survive conversion to ASCII or monochrome SVG

## References (tone, not style)

Tools and publications whose visual identity carries the right weight:
- The Guardian's investigative unit visual language
- ProPublica's identity (serious, editorial)
- OCCRP's mark (investigative journalism, international)
- Classic Unix/Linux tool aesthetics (htop, vim, tmux)

## Open question

If the dog mark proves too complex to reduce to a clean ASCII rendering, a strong typographic mark (stylised "W" or "WD" monogram) with a minimal dog silhouette accent is a viable fallback. The ASCII version would then be a typographic banner rather than a pictorial one — many respected CLI tools go this route. The brief author has no strong preference; the ASCII constraint is the deciding factor.
