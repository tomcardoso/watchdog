---
description: a YouTube video transcript, podcast transcript, broadcast transcript, recorded speech, press conference or earnings call transcript, or similar audio or video-derived text; for official verbatim proceeding records (Hansard, committee transcripts, court or deposition transcripts) use `legislature-transcripts`, `court-documents`, or `criminal-proceedings`
---
# Domain knowledge — Audio and video content

This skill is loaded by Watchdog when the document type is a YouTube video transcript, podcast transcript, broadcast transcript, recorded speech, press conference or earnings call transcript, or similar audio or video-derived text. Official verbatim proceeding records are covered elsewhere: Hansard and legislative committee transcripts by `legislature-transcripts`; court and deposition transcripts by `court-documents` and `criminal-proceedings`.

---

## Document types covered

- YouTube video transcripts (auto-generated or manual)
- Podcast episode transcripts
- Broadcast news transcripts (TV, radio)
- Press conference transcripts
- Public speech transcripts
- Earnings call transcripts
- Investor day presentation transcripts
- Corporate AGM transcripts
- Social media video transcripts (Twitter/X Spaces, Facebook Live, Instagram)

---

## Fields to extract

| Field | What to look for |
|-------|-----------------|
| **Title / episode name** | The video or podcast title |
| **Channel / show** | The YouTube channel, podcast name, or broadcaster |
| **Speaker(s)** | Every named participant and their role |
| **Publication date** | When the content was published or recorded |
| **URL or identifier** | The link or permanent identifier for the source |
| **Transcript type** | Auto-generated, manual, professional |
| **Duration** | Length of the content (helps assess what might be missing) |
| **Key statements** | Verbatim quotes of significant factual claims or admissions |
| **Named parties** | Every person, company, or place mentioned |
| **Documents referenced** | Any documents, reports, or data the speaker cites |
| **Timestamps** | For significant statements: approximate timestamp for verification |

---

## Red flags and reliability considerations

### Transcript quality

- **Auto-generated transcripts** — when the transcript is marked as, or observably appears to be, machine-generated (e.g. a YouTube auto-caption), record that fact and treat the exact wording — especially proper nouns, technical terms, names, and numbers — as unverified. Capture the quote but flag the wording as requiring confirmation against the audio.
- **Edited or incomplete transcripts** — a manually prepared transcript may have been selectively transcribed. Note if sections are marked "[inaudible]", "[crosstalk]", or "[portion omitted]" — these gaps may be meaningful.
- **Speaker identification errors** — capture speaker attributions exactly as stated in the transcript. In multi-speaker settings, log a lead to verify the attributions against any available video where speakers are visible.
- **Translated transcripts** — a transcript translated from another language carries compounded reliability risk (transcription errors + translation errors). Note the source language.

### Content assessment

- **Admissions against interest** — statements by a company executive, government official, or subject of an investigation that acknowledge wrongdoing, awareness of a problem, or inconsistency with public positions. These can be the highest-value content in an audio/video source.
- **Statements that contradict public positions** — when a recorded statement contradicts a public position already recorded on an entity in the vault digest (e.g. an executive says "we have known about this risk for two years" while the digest records the company's public position that the problem was unexpected), record both sides and flag the contradiction. Where no such prior position is in the digest, capture the statement and log a lead to check it against the entity's public record.
- **Date of statement relative to events** — the date the content was recorded establishes what the speaker knew and said at that time.
- **Statements made before an incident vs. after** — a podcast recorded before a company's collapse or a political scandal may contain statements the speaker would not make now.

### Earnings calls and investor presentations

- **Forward-looking statements and disclaimers** — earnings calls include boilerplate "safe harbour" disclaimers protecting the company from liability for forward-looking statements. The disclaimer is standard; evaluate what was actually said on its merits.
- **Analyst questions that reveal market concern** — analyst questions in earnings calls often surface problems the company hasn't addressed publicly. The questions themselves could be of journalistic interest even when the answers are evasive.
- **Guidance revisions** — a company that revised its guidance downward, or withdrew guidance entirely, without adequate prior disclosure.

### Podcasts and YouTube

- **Self-published content without editorial oversight** — a YouTube video or podcast published directly by a subject is unedited; statements are less guarded than in a press interview. This makes them more valuable for direct quotes but requires the same source assessment as any self-interested communication.
- **Comments and community posts** — the comments section of a YouTube video and community posts by the channel may contain additional context or subsequent clarifications.
- **Previously published and removed content** — if the document or its surrounding context states that the content was previously published and later removed, record the stated date and platform and log a lead for an archive search (Internet Archive, Odysee, or other mirror sites).

---

## How to use audio and video content in an investigation

Audio and video content is most useful as:

1. **Direct quote source** — a verbatim statement by the subject, verifiable from the original recording. More difficult to dispute than a paraphrase in a document.
2. **Timeline evidence** — establishing what a person knew, said, or claimed on a specific date.
3. **Corroboration** — a statement in a video that matches or contradicts a documentary record.
4. **Lead generation** — a podcast guest who describes an event, names a person, or references a document that can be pursued through primary sources.

For journalism: audio and video quotes require verification against the original recording. An auto-generated transcript is a finding aid, not a quotation source.

---

## Terminology

| Term | Meaning |
|------|---------|
| **Auto-caption / auto-generated transcript** | A machine-generated transcript from YouTube or a similar platform — lower reliability, especially for proper nouns |
| **Verbatim transcript** | A word-for-word transcript, including false starts, hesitations, and interjections |
| **Clean transcript** | A transcript that has been edited to remove hesitations and false starts for readability |
| **[Inaudible]** | A portion of the audio that the transcriber could not hear clearly |
| **[Crosstalk]** | Simultaneous speech by multiple participants — content may be unrecoverable |
| **Timestamp** | The time position in the recording (e.g. 14:32) — always record timestamps for significant quotes |
| **Earnings call** | A quarterly conference call in which a public company's management discusses financial results with analysts |
| **Safe harbour statement** | A legal disclaimer preceding forward-looking statements in earnings calls |
| **Spaces** | Twitter/X audio broadcasts — ephemeral by default but sometimes recorded |
| **Wayback Machine** | archive.org's web archiving service — can preserve YouTube pages but not the video itself (YouTube-DL family of tools is needed for video) |

---

## Relationships to extract from audio and video content

1. **Person → Statement**: Who said what, in which recording, on what date (with timestamp)
2. **Person → Show/Channel**: Host, guest, or participant in the recorded content
3. **Person → Organization**: Role of the speaker at time of recording
4. **Statement → Document**: Reference to a document or data source cited in the recording
5. **Statement → Event**: A claim that relates to a known event (date, outcome, prior knowledge)

---

## What investigators typically miss

1. **The timestamp** — always record the timestamp for any significant quote. The timestamp allows anyone to verify the quote against the original recording and makes the citation credible.
2. **The publication date vs. the recording date** — a podcast published today may have been recorded weeks ago. If the content refers to events, check whether those events had occurred by the recording date.
3. **The full context of a clip** — a clip or excerpt may be decontextualized. Always listen to the surrounding context before treating a statement as significant.
4. **Deleted content** — a video or episode that has been removed from YouTube or a podcast feed may be archived at archive.org, on a mirror site, or in a listener's cache. The fact of deletion is itself significant.
5. **Earnings call transcripts via free services** — Seeking Alpha, Motley Fool, and company investor relations pages often publish earnings call transcripts.
6. **Foreign-language content** — a YouTube channel or podcast in another language may contain significant statements not captured by English-language research. Auto-translation in YouTube can give a rough understanding; professional translation is needed for direct quotes.
7. **Tone and language shifts across successive calls** — a significant change in how an executive discusses a specific topic (from confident to hedged, from detailed to vague) across successive earnings calls may signal deterioration. This requires comparing the current call against prior calls; log a lead to pull earlier transcripts and compare.

---

## Sources and further reading

### Practitioner and public interest

- [WeVerify / InVID Verification Plugin — weverify.eu](https://weverify.eu) — Open-source browser extension and platform for verifying video and image content; includes reverse video search, near-duplicate detection, deepfake detection, and metadata analysis
- [WITNESS Media Lab — lab.witness.org](https://lab.witness.org/about/) — Research and practice hub from WITNESS focused on sourcing, verifying, and contextualizing eyewitness video for human rights documentation and journalism
- [TinEye Reverse Image Search — tineye.com](https://tineye.com) — Reverse image search engine for tracing the origin and prior appearances of images and video frames; use to establish whether an image is recycled from an earlier event

### Journalism resources

- [Bellingcat — Advanced Guide on Verifying Video Content](https://www.bellingcat.com/resources/how-tos/2017/06/30/advanced-guide-verifying-video-content/) — Covers techniques for authenticating user-generated video including reverse image search, visual analysis, and detection of manipulation such as mirroring or colour changes
- [Bellingcat — A Beginner's Guide to Social Media Verification](https://www.bellingcat.com/resources/2021/11/01/a-beginners-guide-to-social-media-verification/) — Five-step framework (originality, source, location, time, motivation) for verifying photos and videos shared on social platforms
- [First Draft — Verifying Online Information](https://firstdraftnews.org/long-form-article/verifying-online-information/) — Journalist's guide to verifying digital content; covers InVID integration, metadata analysis, and geolocation techniques for video and audio
