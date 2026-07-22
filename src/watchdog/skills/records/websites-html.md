---
description: a captured web page — a corporate or organizational website, personal or professional profile, government agency page, online directory, forum post, social media post, blog, marketplace or job listing, or an archived/Wayback Machine snapshot of any of these
---
# Domain knowledge — Websites and HTML pages

This skill is loaded by Watchdog when the document is a general web page captured via `watchdog research` or `watchdog fetch` — a corporate or organizational website, a personal or professional profile, a government agency page, an online directory, a forum post, a social media post, a blog, or a marketplace or job listing. It does not cover news articles, wire stories, or press releases (see `news-clippings`), WHOIS/DNS/infrastructure records (see `dns-whois`), or audio/video transcripts (see `audio-video`) — those document types are owned by their own skills even when captured through the same web-research pipeline.

---

## Document types covered

- Corporate and organizational websites (About, Team, Contact, Products/Services pages)
- Personal or professional profile and bio pages
- Government or public-body web pages that are not themselves a filing, report, or transcript
- Online directories and registries rendered as web pages (chamber-of-commerce listings, professional directories, business registries with a web front end)
- Forum posts, message boards, and comment sections
- Social media posts and profile pages
- Blogs (personal, corporate, or advocacy — non-journalistic)
- Marketplace and e-commerce listings
- Job postings
- Archived or cached snapshots of any of the above (Wayback Machine, archive.today, search-engine cache)

---

## Fields to extract

| Field | What to look for |
|-------|-----------------|
| **Page title / headline** | The page's own title, as rendered or in its `<title>`/heading |
| **Site or organization name** | The website's or account's stated owner or operator |
| **URL** | The source URL — carried in the sidecar's `source` field |
| **Page type** | About, Team, Contact, Product/Service, Forum post, Profile, Listing, Job posting, etc. |
| **Author or poster** | Byline, username, or handle, if attributed |
| **Publication / last-modified date** | Any date the page itself displays (post date, "last updated", copyright year) |
| **Date captured** | The `obtained` date in the sidecar — when Watchdog fetched the page, distinct from any date on the page |
| **Capture method** | The sidecar's `capture` field — `rendered` (full Chromium snapshot) or `plain` (script-stripped fetch); a `plain` capture may be missing JavaScript-loaded content |
| **Archived snapshot** | The sidecar's `archived` field, if present — a Wayback Machine URL for this capture |
| **Named individuals and roles** | Anyone named on the page, with their stated title or role |
| **Contact details** | Address, phone number, email shown on the page |
| **Claims made** | What the page asserts about the organization, product, or subject |
| **Links to other documents** | Filings, reports, or other pages the page links to or cites |

---

## Red flags — what to look for

### Provenance and capture

- **This is a scraped secondary source, not a primary document** — a captured web page reflects what was published at the moment of capture, not necessarily what is true. Record the capture date (`obtained`) and treat the page's claims as attributed to the page, not established fact.
- **Capture method may mean missing content** — when the sidecar's `capture` field is `plain` rather than `rendered`, dynamic content (a "load more" feed, a script-populated table, an embedded widget) may not appear in the captured text. Note when the visible content looks truncated or a described feature (a gallery, a list) is absent — this may be a capture limitation, not an absence on the live page.
- **Page has an archived history** — when the sidecar carries an `archived` (Wayback Machine) field, record it; a captured page with a citable permanent snapshot is stronger provenance than one without.
- **Retrieval path** — record the sidecar's `retrieved_by` (`research-mode` vs `fetch`) and `source_type` tag (official-registry, news, blog, forum, social, etc.) as given; these set the reliability frame the extractor already has, rather than something to redetermine from the page text.

### Self-description and claims

- **Self-published claims about the subject itself** — an "About Us" page, a company bio, or a profile describes its subject in its own words. Record what is claimed ("family owned since 1985", "no history of complaints", "industry-leading compliance") as a stated self-description, not a verified fact.
- **Contact details matching or conflicting with the entity digest** — an address, phone number, or email on the page that matches a value already recorded on an entity in the digest is a strong link; one that conflicts with a value the digest already states for the same entity is worth recording as a contradiction. Both sides must be explicitly stated to count as a conflict.
- **Team or leadership bios naming individuals not elsewhere in the vault** — capture each named person and their stated title; a name on a "leadership" or "team" page is often the first mention of someone worth a full profile.
- **Testimonials, reviews, and endorsements** — record who or what is credited (a named person, "a satisfied customer", an unnamed reviewer) and what is claimed; the extractor cannot verify authenticity, so note anonymous or unverifiable attribution as such.

### Web-specific patterns

- **Look-alike branding or naming** — a site whose name, logo description, or copy closely mirrors a known organization (a government department, a bank, a well-known company) may be designed to look official when it isn't. This is visible in the page's own text and styling description; record the resemblance as stated.
- **Anonymous or pseudonymous authorship** — forum posts, comments, and some blog posts carry only a username or handle. Record the handle as given; do not assume it identifies a real person unless the page itself states one.
- **Job postings revealing operational detail** — a job posting can disclose expansion plans, new office locations, technology in use, headcount growth, or compensation bands that the organization doesn't state elsewhere. Record these as stated facts about the posting.
- **Allegations in comments or forum replies** — a comment section or forum thread sometimes contains claims more direct than the page it's attached to (an accusation, an insider account). Record the claim and its unverified, anonymous or pseudonymous source; this is a lead, not a finding.
- **Marketplace listing details** — price, seller identity, item condition, and location on a marketplace or e-commerce listing are often the only public trace of a transaction or asset; record them as stated.

---

## Terminology

| Term | Meaning |
|------|---------|
| **Wayback Machine** | The Internet Archive's public web archive; stores dated snapshots of pages, useful for tracking how a page changed over time |
| **Save Page Now (SPN2)** | The Wayback Machine's on-demand archiving API; Watchdog can use it to archive a source at capture time (see the sidecar's `archived` field) |
| **Cache / cached page** | A search engine's or archiving tool's stored copy of a page, sometimes retrievable after the live page is edited or removed |
| **Canonical URL** | The URL a page declares as its authoritative address, which may differ from the address it was actually fetched at |
| **Open Graph tags / meta tags** | Structured metadata embedded in a page's HTML (title, description, image) used by social platforms and search engines, sometimes containing details not shown in the rendered page |
| **Rendered capture** | A full browser (Chromium) snapshot of a page after scripts run, preserving JavaScript-loaded content; Watchdog's preferred capture mode |
| **Plain capture** | A sanitized fetch of a page's raw HTML with scripts stripped but not executed; may miss content that only appears after a script runs |
| **DOM** | Document Object Model — the structured representation of a page's content that a browser builds; what a rendered capture preserves |
| **Permalink** | A stable, dedicated URL for one piece of content (a post, a comment) intended not to change |
| **UGC (user-generated content)** | Content posted by a platform's users rather than its operator — forum posts, reviews, comments |
| **Sock puppet** | A fake or secondary online identity used to post reviews, comments, or votes to create a false impression of independent support |
| **Astroturfing** | Coordinated, often anonymous or pseudonymous online activity made to look like spontaneous grassroots opinion |

---

## Relationships to extract

1. **Person → Organization**: Role or title stated on a team, staff, or "About" page
2. **Organization → Place**: Address shown on a contact or "About" page
3. **Person → Person**: Named together on the same page (co-authors, co-founders, quoted alongside one another)
4. **Website/Domain → Organization**: Stated operator or publisher of the site (cross-reference the `dns-whois` skill for the registrant of the underlying domain)
5. **Person → Document**: A document, filing, or report the page links to or cites

Use `→` notation. Include the relationship type after the colon.

---

## What investigators typically miss

1. **The Wayback Machine's full crawl history, not just one snapshot** — a single captured page is one moment; the Wayback Machine's calendar view of a URL can show when text, staff bios, or claims were added, changed, or quietly removed.
2. **Search-engine or archive.today cache of a page since taken down** — a page that returns a 404 today may still be retrievable through a cache or a second archiving service, especially useful when a subject deletes a page shortly after being contacted.
3. **Page metadata beneath the visible text** — meta tags, Open Graph data, and structured data (JSON-LD) can carry an author name, a publish date, or an internal document ID not shown in the rendered page; worth checking "view source" directly.
4. **A site's sitemap or robots.txt** — these can reveal pages that exist but aren't linked from the visible navigation, including pages the operator would rather not surface.
5. **Reverse image search on staff photos or logos** — a "team" page whose headshots are stock photography, or a logo copied from another organization, is a strong signal of a fabricated or shell front; this requires an image search the extractor cannot perform, so log it as a lead.
6. **Template or copy reused across differently named sites** — near-identical design, boilerplate text, or even typos repeated across sites presented as unrelated organizations can indicate a network of fronts run by the same operator.
7. **The domain's registration and hosting history** — a website's content is one layer; who registered and hosts the domain is a separate, often more revealing record, covered by the `dns-whois` skill.
8. **Language and locale versions** — a multinational or foreign-linked organization's site often has versions in other languages that carry different, sometimes more candid, content than the English version.

---

## Sources and further reading

### Practitioner and public interest

- [Internet Archive Wayback Machine](https://web.archive.org) — Free, searchable web archive; the primary tool for viewing a page's history and recovering removed content
- [archive.today](https://archive.ph) — Independent on-demand archiving service, useful as a second archive when the Wayback Machine lacks a snapshot or a site blocks its crawler

### Journalism resources

- [Bellingcat's Online Investigation Toolkit](https://bellingcat.gitbook.io/toolkit) — Community-maintained catalogue of OSINT tools, including web-archiving, reverse image search, and social media verification resources
- [First Draft — Verifying Online Information](https://firstdraftnews.org/long-form-article/verifying-online-information/) — Practical guide to verifying web content, including provenance, authorship, and date checks

**Notes on unsourced claims:** The observation that reused design templates or boilerplate text across differently branded sites can indicate a network of fronts (under *What investigators typically miss*) reflects practitioner experience from investigative reporting rather than a single citable source.
