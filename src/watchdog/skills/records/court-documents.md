# Domain knowledge — Court documents

Loaded by `/ingest` when the document type is a statement of claim, affidavit, judgment, court order, or similar legal proceeding record.

---

## Document types covered

- Statements of claim and defence (or complaints and answers)
- Affidavits and sworn declarations
- Court orders and injunctions
- Judgments (trial and appellate)
- Notices of motion and motion records
- Certificates of pending litigation
- Writs of execution
- Bankruptcy orders and receiving orders
- Regulatory tribunal decisions

---

## Always-present fields to extract

| Field | What to look for |
|-------|-----------------|
| **Court file number** | Unique identifier for the proceeding — appears at the top of every document |
| **Court and jurisdiction** | Which court and country/province/state |
| **Parties** | Full legal name of every plaintiff, defendant, applicant, respondent |
| **Counsel** | Lawyers for each party — name, firm, and which party they represent |
| **Date of document** | Date the document was sworn, issued, or filed |
| **Relief sought** | What the plaintiff/applicant is asking the court to do |
| **Monetary amounts** | All dollar figures — damages claimed, amounts in dispute, costs awarded |
| **Addresses** | All addresses referenced — residential, business, registered |
| **Properties** | Any real property described, including legal description |

---

## Red flags

### Party patterns

- **Same individual appearing as plaintiff in multiple unrelated cases** — serial litigant or a person who frequently finds themselves in disputes.
- **Company as defendant in multiple cases** — pattern of non-payment, breach of contract, or regulatory violation. Cross-reference case types.
- **Director or officer named personally** — piercing the corporate veil is significant; it means a court found or a plaintiff alleges personal liability.
- **Party described as "also known as"** — AKAs in court documents are legally significant and should be captured as aliases.
- **Amended pleadings** — if an amended statement of claim exists, compare to the original. Additions and deletions are often where the real story develops.

### Monetary patterns

- **Claim far exceeding apparent company size** — a large claim against a company with modest revenue raises questions about either the claim or the company's solvency.
- **Costs awards** — a costs award against a party signals the court found their position frivolous or their conduct improper.
- **Prejudgment interest claimed from a very early date** — indicates long-running dispute or early notice of the issue.

### Procedural red flags

- **Default judgment** — the defendant never responded. Either they couldn't be found, couldn't afford counsel, or knew they had no defence.
- **Consent order or consent judgment** — the parties agreed. Read the terms carefully: these often contain non-disclosure or non-disparagement terms that are worth noting.
- **Proceeding struck or dismissed for delay** — plaintiff abandoned the case after starting it. Why?
- **Case transferred between courts or jurisdictions** — may indicate forum shopping.
- **Sealing order or publication ban** — something in this case cannot be reported. The existence of the order is itself newsworthy.

---

## Terminology

| Term | Meaning |
|------|---------|
| **Statement of claim** | Document that starts a lawsuit; sets out the plaintiff's allegations and damages sought (called "complaint" in the US) |
| **Statement of defence** | The defendant's response (called "answer" in the US) |
| **Affidavit** | A sworn written statement; the deponent swears it is true |
| **Deponent** | The person who swears an affidavit |
| **Exhibits** | Documents attached to an affidavit and referred to in it |
| **Cross-examination** | Questioning a deponent on their affidavit |
| **Interlocutory order** | An order made during the proceeding, before final judgment |
| **Injunction** | A court order requiring someone to do or not do something |
| **Mareva injunction** | Freezes assets pending judgment — very significant |
| **Anton Piller order** | Allows a party to enter premises and seize evidence without notice (called a "search order" in some jurisdictions) |
| **Summary judgment** | Judgment without a full trial, where there is no genuine issue requiring trial |
| **Without prejudice** | Communications made in settlement discussions; generally not admissible |
| **Costs** | Court's award of legal fees, usually against the losing party |
| **Certificate of pending litigation (CPL)** | Registered against a property to signal a lawsuit affecting title |

## Court hierarchy examples

**Canada (Ontario):** Ontario Court of Justice → Superior Court of Justice → Divisional Court → Court of Appeal for Ontario → Supreme Court of Canada

**Federal Court (Canada):** Federal Court → Federal Court of Appeal → Supreme Court of Canada

**United States (federal):** US District Court → US Court of Appeals (Circuit) → US Supreme Court

**United Kingdom:** Magistrates Court / County Court → High Court → Court of Appeal → UK Supreme Court

**Australia:** Magistrates/Local Court → District/County Court → Supreme Court → Court of Appeal → High Court of Australia

---

## Relationships to extract

1. **Person → CourtCase**: Plaintiff, Defendant, Applicant, Respondent, Deponent, Third Party
2. **Company → CourtCase**: Same roles as above
3. **Person → Company**: Lawyer acting for [Company] in [CourtCase]
4. **Person / Company → Address**: All addresses in pleadings — these may be the only record of a party's address at a specific point in time
5. **CourtCase → Property**: Any property that is the subject of the proceeding
6. **CourtCase → Transaction**: Any transaction alleged in the claim (amounts, dates, parties)

---

## What investigators typically miss

1. **The exhibit list in affidavits** — affidavits attach documents as exhibits. Those documents may be more important than the affidavit itself. Note every exhibit described, even if you don't have the actual exhibit.
2. **Third-party claims** — a defendant can sue a third party they claim is responsible. This is a separate claim embedded in the same file.
3. **Counterclaims** — the defendant may be suing the plaintiff back. Always check whether a statement of defence includes a counterclaim.
4. **The date of service vs. the date of filing** — a document filed on one date may have been served on a different date. The gap can be significant.
5. **Lawyers switching firms mid-proceeding** — indicates something changed in the relationship between the client and the firm. Worth noting.
6. **The litigation history of key parties** — a search for the party's name in court records may reveal a pattern across multiple proceedings.
