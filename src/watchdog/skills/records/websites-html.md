---
description: an HTML file or a downloaded/captured website page, of any kind
---
# Domain knowledge — Websites and HTML pages

This skill is loaded by Watchdog for any HTML document or captured website page — not only pages deposited into `_INCOMING/` by `watchdog research` or `watchdog fetch`, but any website page that enters the vault by any means. It does not cover news articles, wire stories, or press releases (see `news-clippings`), WHOIS/DNS/infrastructure records (see `dns-whois`), or audio/video transcripts (see `audio-video`) — those document types are owned by their own skills even when the underlying file is HTML.

An HTML document carries two layers a reporter can read: the **presentation content** (the text, images, and structure a visitor sees) and the **markup and code beneath it** (tags, scripts, comments, embedded identifiers, links). The second layer is at least as revealing as the first and is easy to skip past — this skill weights both.

---

## Document types covered

- Any HTML file or web page, of any subject or purpose
- Rendered (full browser/Chromium) or plain sanitized-fetch captures of a website
- Archived or cached snapshots of a web page (Wayback Machine, archive.today, search-engine cache)
- Website pages added to the vault by any means, not only through `watchdog research`/`watchdog fetch`

---

## Fields to extract

| Field | What to look for |
|-------|-----------------|
| **Page title** | The page's own title, as rendered or in its `<title>` element |
| **Site or organization name** | The website's stated owner, operator, or account name |
| **URL** | The source URL — carried in the sidecar's `source` field, or a `canonical` link element in the markup |
| **Author or poster** | Byline, username, handle, or `author` meta tag, if present |
| **Publication / last-modified date** | Any date the page displays, or a date in its metadata (`article:published_time`, `article:modified_time`, copyright year) |
| **Date captured** | The `obtained` date in the sidecar — when Watchdog fetched the page, distinct from any date on or in the page |
| **Capture method** | The sidecar's `capture` field — `rendered` (full Chromium snapshot) or `plain` (script-stripped fetch); a `plain` capture may be missing JavaScript-loaded content |
| **Archived snapshot** | The sidecar's `archived` field, if present — a Wayback Machine URL for this capture |
| **Platform / generator** | A CMS or site-builder fingerprint visible in the markup — a `generator` meta tag, a platform-specific asset path (`/wp-content/`, `/cdn.shopify.com/`), or template markers |
| **Tracking and analytics identifiers** | Any analytics, tag-manager, or pixel ID embedded in the page (see red flags below) |
| **Outbound links** | Every hyperlink target, grouped by destination domain |
| **Hidden or non-rendered elements** | Any element the markup itself marks as not meant to be seen (see red flags below) |
| **Embedded structured data** | `JSON-LD`, Open Graph, or other structured metadata blocks in the `<head>` |
| **Claims made** | What the page's text asserts about its subject |

---

## Red flags — what to look for

### Markup and code

- **Analytics and tracking identifiers** — a Google Analytics measurement ID (`G-XXXXXXX`/`UA-XXXXXXXX`), a Google Tag Manager container ID (`GTM-XXXXXXX`), a Meta/Facebook Pixel ID, or any other tracking snippet's account ID is a stable identifier the page's markup carries whether or not the visible content mentions it. Record every such ID exactly as it appears. **The same ID appearing on two pages presented as unrelated sites or organizations is one of the strongest links available** — capture it and compare against the entity digest and against IDs seen on other captured pages.
- **Platform or CMS fingerprint** — a `<meta name="generator">` tag, a template-specific path (`/wp-content/`, `/wp-json/`, a Substack or Squarespace asset URL, a Shopify CDN path), or boilerplate class names reveal what software built the site. Record the platform as stated; a claimed enterprise operation running on a template starter site, or two "independent" sites sharing an identical, uncommon template fingerprint, is worth flagging.
- **Hidden or non-rendered content** — an element whose inline style or class sets it to not display (`display:none`, `visibility:hidden`, zero height/width, off-screen positioning), a hidden `<input>` field carrying a value, or text present in the markup that a visitor would never see. Record the hidden content verbatim and where it sits in the page; content authored for a reader but hidden from one (or authored for a search engine or bot but hidden from a human, i.e. cloaking) is itself a fact worth capturing, not something to explain away.
- **Outbound links across domains** — collect every hyperlink's destination domain. A page's outbound-link pattern (which domains it sends visitors to) can reveal an affiliate network, a syndication relationship, an advertising partner, or a shared operator behind pages that otherwise look unrelated. Record the set of external domains linked; a link target that recurs across pages already in the digest is a match worth flagging.
- **HTML comments and leftover markup** — text inside `<!-- -->` comments is invisible to a visitor but present in the source: developer notes, TODOs, an earlier draft of a claim, or content commented out rather than deleted. Record any comment with substantive content.
- **Meta tags that contradict the visible page** — a `robots` meta tag or header set to `noindex`/`nofollow` means the page's operator asked search engines not to surface it; combined with content that reads as intended for public visibility, this is worth recording as a stated fact about how the page was configured. Likewise record a `canonical` link element that points to a different URL than the one captured — it names what the page's own markup claims is the authoritative version.
- **Embedded third-party scripts and widgets** — a chat widget (Intercom, Zendesk, Drift), a payment processor (Stripe, PayPal), or an ad network's script tag names a vendor relationship the visible page may not mention. Record the vendor and any account/site ID the embed carries.
- **Inline data blobs** — some pages embed a JSON blob directly in a `<script>` tag (e.g. a `window.__INITIAL_STATE__` or similar bootstrap object) carrying structured data — internal IDs, API endpoints, or content not otherwise rendered. Record anything substantive found there.

### Provenance and capture

- **This is a scraped secondary source, not a primary document** — a captured web page reflects what was published at the moment of capture, not necessarily what is true. Record the capture date (`obtained`) and treat the page's claims as attributed to the page, not established fact.
- **Capture method may mean missing content** — when the sidecar's `capture` field is `plain` rather than `rendered`, dynamic content (a script-populated table, a "load more" feed, an embedded widget) may not appear in the captured markup. Note when visible content looks truncated, or a described feature is absent — this may be a capture limitation, not an absence on the live page.
- **Retrieval path** — record the sidecar's `retrieved_by` (`research-mode` vs `fetch`) and `source_type` tag, where present, as given; these set the reliability frame the extractor already has, rather than something to redetermine from the page text.

### Presentation content

- **Self-published claims about the subject itself** — a page describes its subject in its own words. Record what is claimed as a stated self-description, not a verified fact.
- **Contact details matching or conflicting with the entity digest** — an address, phone number, or email on the page that matches a value already recorded on an entity in the digest is a strong link; one that conflicts with a value the digest already states for the same entity is worth recording as a contradiction. Both sides must be explicitly stated to count as a conflict.
- **Named individuals** — capture each person named on the page and their stated title or role; a name on a page is often the first mention of someone worth a full profile.
- **Anonymous or pseudonymous authorship** — a post, comment, or listing that carries only a username or handle. Record the handle as given; do not assume it identifies a real person unless the page itself states one.

---

## Terminology

| Term | Meaning |
|------|---------|
| **DOM** | Document Object Model — the structured tree of elements a browser builds from a page's markup; what a rendered capture preserves |
| **Rendered capture** | A full browser (Chromium) snapshot of a page after scripts run, preserving JavaScript-loaded content; Watchdog's preferred capture mode |
| **Plain capture** | A sanitized fetch of a page's raw HTML with scripts stripped but not executed; may miss content that only appears after a script runs |
| **Generator meta tag** | A `<meta name="generator">` element some CMS/site builders add automatically, naming the software that produced the page |
| **Google Analytics ID / GA4 measurement ID** | An identifier (`UA-XXXXXXXX` or `G-XXXXXXX`) tying a page's traffic data to a specific Google Analytics account; the same ID across sites indicates the same account holder |
| **Google Tag Manager (GTM) container ID** | An identifier (`GTM-XXXXXXX`) for a tag-management container; like an analytics ID, shared use across sites is a strong operator link |
| **Meta/Facebook Pixel** | Meta's tracking snippet, identified by a numeric Pixel ID, used for ad targeting and conversion tracking |
| **Canonical link** | A `<link rel="canonical">` element declaring the URL a page considers its authoritative address, which may differ from the address actually fetched |
| **Robots meta tag / noindex / nofollow** | Markup instructing search engines whether to index a page or follow its links; `noindex` signals the operator did not want the page found through search |
| **Cloaking** | Serving different content to search engines/bots than to human visitors, or hiding content from visitors while leaving it in the markup — a technique associated with SEO manipulation |
| **Open Graph tags** | Meta tags (`og:title`, `og:image`, etc.) that control how a page appears when shared on social platforms, sometimes containing details not shown in the rendered page |
| **JSON-LD** | Structured data embedded in a `<script type="application/ld+json">` block, machine-readable metadata about the page's content |
| **Wayback Machine** | The Internet Archive's public web archive; stores dated snapshots of pages, useful for tracking how a page changed over time |
| **UGC (user-generated content)** | Content posted by a platform's users rather than its operator — forum posts, reviews, comments |

---

## Relationships to extract

1. **Website → Website**: Shared analytics/tracking identifier, embedded-script vendor, or generator/template fingerprint (a markup-level match between two otherwise unrelated pages)
2. **Person → Organization**: Role or title stated on the page
3. **Organization → Place**: Address shown on the page
4. **Website/Domain → Organization**: Stated or implied operator of the site (cross-reference the `dns-whois` skill for the registrant of the underlying domain)
5. **Website → Website**: Outbound link to another domain (partner, affiliate, syndication, or advertising relationship)

Use `→` notation. Include the relationship type after the colon.

---

## What investigators typically miss

1. **Reading the raw markup, not just the rendered page** — view-source (or the equivalent in a captured file) surfaces analytics IDs, hidden elements, comments, and platform fingerprints that never appear in the rendered text a casual reading captures.
2. **A shared analytics or tag-manager ID across "unrelated" sites** — this is one of the most reliable ways to tie a network of seemingly independent websites to a single operator, and it requires nothing more than comparing an ID string across pages.
3. **The Wayback Machine's full crawl history, not just one snapshot** — a single captured page is one moment; a URL's calendar view can show when text, markup, or tracking tags were added, changed, or quietly removed.
4. **Search-engine or archive.today cache of a page since taken down** — a page that 404s today may still be retrievable through a cache or a second archiving service, especially useful when a subject deletes a page shortly after being contacted.
5. **A site's sitemap or robots.txt** — these can reveal pages that exist but aren't linked from the visible navigation, including pages the operator marked `noindex` or would rather not surface.
6. **Reverse image search on photos or logos** — a page whose images are stock photography, or a logo copied from another organization, is a strong signal the extractor cannot verify itself; this requires an image search, so log it as a lead.
7. **Template or markup reused across differently named sites** — near-identical generator fingerprints, class names, or even code comments repeated across sites presented as unrelated organizations can indicate a network of fronts run by the same operator.
8. **The domain's registration and hosting history** — a page's markup is one layer; who registered and hosts the domain is a separate, often more revealing record, covered by the `dns-whois` skill.

---

## Sources and further reading

### Practitioner and public interest

- [Internet Archive Wayback Machine](https://web.archive.org) — Free, searchable web archive; the primary tool for viewing a page's history and recovering removed content
- [archive.today](https://archive.ph) — Independent on-demand archiving service, useful as a second archive when the Wayback Machine lacks a snapshot or a site blocks its crawler
- [BuiltWith](https://builtwith.com) — Technology-profiling tool that identifies a site's CMS, analytics, and tracking tags from its markup; useful for confirming a platform fingerprint or comparing tracking IDs across domains

### Journalism resources

- [Bellingcat's Online Investigation Toolkit](https://bellingcat.gitbook.io/toolkit) — Community-maintained catalogue of OSINT tools, including web-archiving, reverse image search, and social media verification resources
- [First Draft — Verifying Online Information](https://firstdraftnews.org/long-form-article/verifying-online-information/) — Practical guide to verifying web content, including provenance, authorship, and date checks

**Notes on unsourced claims:** The observation that a shared analytics/tag-manager ID or reused template markup across differently branded sites can indicate a single operator or a network of fronts (under *Markup and code* and *What investigators typically miss*) reflects a technique documented in open-source investigation practice (e.g. Bellingcat's toolkit) rather than a single canonical citation.
