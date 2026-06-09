# Domain knowledge — Immigration and refugee documents

This skill is loaded by `/ingest` when the document type is an immigration tribunal decision, asylum ruling, deportation order, refugee protection decision, or related immigration record.

Apply this knowledge in addition to the standard extraction process. It tells you what to look for, what terminology means, and what patterns are worth flagging.

---

## Document types covered

- First-instance refugee / asylum decisions
- Appeal tribunal decisions on refugee and asylum claims
- Immigration detention reviews
- Deportation and removal orders
- Judicial review decisions (where immigration decisions are reviewed by courts)
- Pre-removal risk assessments
- Humanitarian and compassionate applications
- In Canada: Immigration and Refugee Board (IRB) decisions — RPD, RAD, IAD, ID divisions; Federal Court judicial reviews; Pre-removal risk assessment (PRRA) decisions; H&C applications
- In the US: Immigration court decisions (EOIR); asylum decisions (USCIS); Board of Immigration Appeals (BIA) decisions
- In the UK: First-tier Tribunal (Immigration and Asylum Chamber) decisions; Upper Tribunal decisions; Home Office refusal decisions
- In the EU: National asylum authority decisions; European Court of Human Rights decisions on removal cases

---

## Always-present fields to extract

| Field | What to look for |
|-------|-----------------|
| **File number** | The tribunal or court case number — may be anonymized in published versions |
| **Decision date** | When the decision was rendered |
| **Decision-maker** | Member, judge, or officer name and division/court |
| **Claimant / appellant** | Name (often anonymized in published decisions) or initials |
| **Country of origin** | The country from which the claimant is seeking protection |
| **Ground of claim** | The refugee convention ground: race, religion, nationality, political opinion, or particular social group |
| **Decision outcome** | Accepted, rejected, allowed, dismissed, set aside |
| **Credibility findings** | Whether the decision-maker found the claimant credible |
| **Country condition evidence** | Which country condition documents were considered |
| **Risk factors identified** | Specific risks the claimant faces if returned |
| **Removal order type** | Type of order and any re-entry bar or consequences |

---

## Red flags — what to look for

### Decision quality and process

- **Credibility findings based on demeanour** — findings that a claimant is not credible because of how they appeared when testifying (eye contact, emotional response) have been criticized by courts as unreliable and culturally biased. Note any such findings.
- **Boilerplate language** — decisions that appear to use identical paragraphs across multiple cases, particularly on country conditions, may not reflect genuine case-by-case analysis.
- **Failure to consider documentary evidence** — a decision that does not address documentary evidence submitted by the claimant is a ground of judicial review or appeal. Look for references (or the absence of references) to submitted exhibits.
- **Country condition evidence that is outdated** — decisions should use current country condition documentation. A decision relying on evidence that predates a significant political change in the country of origin is vulnerable on review.
- **Failure to consider risk to family members** — some claims involve risk to a claimant's family in the country of origin; failure to address this is an error.

### Detention reviews

- **Length of detention** — most jurisdictions require regular review of immigration detention. Long detentions (months or years) with continued detention orders should be noted along with reasons.
- **Grounds for detention** — flight risk, danger to the public, or identity not established. "Danger to the public" detentions on the basis of criminality should identify the specific offences.
- **Alternative conditions not considered** — the decision-maker must consider conditions (bonds, reporting requirements, GPS monitoring) before ordering continued detention. A failure to do so is an error.
- **Detention of minors** — the detention of children is subject to heightened scrutiny in most jurisdictions; note any cases involving minors.

### Removal orders and enforcement

- **Type of removal order and re-entry consequences** — note the type of removal and any bar on re-entry; different categories carry different consequences.
- **Stay of removal** — a stay pending judicial review or a humanitarian application halts removal. Note whether a stay was sought and whether it was granted or denied.
- **Removal to a country under a travel advisory or with documented human rights concerns** — check whether the country of removal is subject to official travel warnings or is documented as unsafe for the claimant's profile.

---

## Jurisdiction terminology

| Term | Jurisdiction | Meaning |
|------|-------------|---------|
| **IRB** | Canada | Immigration and Refugee Board — Canada's independent administrative tribunal |
| **RPD / RAD / IAD / ID** | Canada | The four IRB divisions: Refugee Protection Division, Refugee Appeal Division, Immigration Appeal Division, Immigration Division |
| **PRRA** | Canada | Pre-removal risk assessment — a last-resort process before removal that assesses new risk evidence |
| **H&C** | Canada | Humanitarian and compassionate application — a request for permanent residence based on establishment in Canada and hardship |
| **Safe Third Country Agreement** | Canada | Canada-US treaty requiring claimants at official ports of entry from the US to claim in the US (with exceptions) |
| **Convention refugee** | Universal | A person meeting the 1951 Refugee Convention definition — faces persecution based on race, religion, nationality, political opinion, or membership in a particular social group |
| **Exclusion clause** | Universal | Article 1F of the Refugee Convention — excludes those who have committed war crimes, crimes against humanity, or serious non-political crimes |
| **EOIR** | US | Executive Office for Immigration Review — administers US immigration courts |
| **BIA** | US | Board of Immigration Appeals — the US appellate body for immigration decisions |
| **USCIS** | US | US Citizenship and Immigration Services — handles asylum applications affirmatively |
| **First-tier Tribunal** | UK | The initial immigration and asylum tribunal in England and Wales |
| **Section 3 ECHR** | UK / Europe | Article 3 of the European Convention on Human Rights — prohibition on torture and inhuman treatment; the principal ground for preventing removal to a dangerous country |
| **National Documentation Package (NDP)** | Canada | Standardized country condition documents compiled by the IRB for each country |

---

## Relationships to extract from immigration records

1. **Person → Country**: Country of origin, country of habitual residence, country of proposed removal
2. **Person → Decision-maker**: Who decided the case and which division or court
3. **Person → Legal representative**: Counsel or consultant who appeared (note: if unrepresented, flag it)
4. **Person → Family members**: Accompanying claimants, family members in the destination country or abroad who affect the claim
5. **Decision → Prior decision**: Any prior decision in the same case (for appeals and judicial reviews)

---

## What investigators typically miss

1. **Anonymization exceptions** — immigration decisions are published with names anonymized as a general rule in many jurisdictions, but decisions involving public figures or public interest matters may be published with names. If you have an un-anonymized version, note that.
2. **The country condition package** — tribunals compile standardized country condition documents. A decision's reliance on those documents versus independent documentary evidence, and whether they were current, is often decisive on review.
3. **The designated or appointed representative** — claimants who are minors or lack capacity must have a designated representative. Note whether one was appointed and who it was.
4. **Credibility vs. inclusion vs. exclusion** — a decision may reject a claim on credibility grounds without reaching the question of whether the country conditions would warrant protection. The order matters: if the decision-maker finds exclusion (Article 1F) first, the claim is dead regardless of risk.
5. **The consultant or legal representative's registration** — immigration consultants must be registered in many jurisdictions. If a claimant was represented by an unregistered consultant, that is significant and may be a ground for re-opening the claim.
6. **Pattern across decisions by the same decision-maker** — a decision-maker who accepts or rejects claims from a particular country at a rate significantly different from the tribunal average is worth examining.

---

## Sources and further reading

### Official and regulatory

- [Immigration and Refugee Board of Canada — Decisions](https://www.irb-cisr.gc.ca/en/decisions/Pages/index.aspx) — Published IRB decisions including persuasive decisions, jurisprudential guides (RAD), and reasons of interest across all four divisions (RPD, RAD, IAD, ID). The full decision set is also on CanLII.
- [IRB — Assessment of Credibility in Claims for Refugee Protection](https://www.irb-cisr.gc.ca/en/legal-policy/legal-concepts/Pages/Credib.aspx) — IRB's own legal guidance on credibility assessment, including principles on demeanour, inconsistencies, implausibility, and trauma; the document that governs what decision-makers are supposed to do and that courts use to evaluate whether they did it.
- [IRB — Procedures and Practice Notices](https://irb-cisr.gc.ca/en/legal-policy/procedures/pages/index.aspx) — Official procedural rules governing how hearings are conducted in each division; essential for understanding what is required and what constitutes procedural error.
- [Immigration, Refugees and Citizenship Canada (IRCC)](https://www.canada.ca/en/immigration-refugees-citizenship.html) — Authoritative source for Canadian immigration law, policy, and program descriptions, including H&C application guidance, pre-removal risk assessment, and the Safe Third Country Agreement.
- [CanLII — Canadian Legal Information Institute](https://www.canlii.org/) — Includes Federal Court and Federal Court of Appeal judicial review decisions on IRB matters; searchable by country of origin, issue, or IRB division for pattern research.
- [College of Immigration and Citizenship Consultants — Public Register](https://register.college-ic.ca/Public-Register-EN/Public-Register-EN/Default.aspx) — Searchable register of all licensed Regulated Canadian Immigration Consultants (RCICs); shows licence status, discipline history, and current standing. Use to verify whether a claimant's representative was authorized.

### Practitioner and public interest

- [UNHCR — Handbook on Procedures and Criteria for Determining Refugee Status](https://www.unhcr.org/media/handbook-procedures-and-criteria-determining-refugee-status-under-1951-convention-and-1967) — The authoritative UNHCR guidance document interpreting the 1951 Refugee Convention definition; published 1979, re-issued 1992 and 2019. Sets the international baseline against which national decision-making is measured.
- [Canadian Council for Refugees — Detention Resources](https://ccrweb.ca/en/issue-pages/detention/resources) — CCR policy submissions, public statements, and practical guides on immigration detention, including the organization's work on child detention and detention alternatives; spans 2016–2024.
- [CLEO — Immigration and Refugee Resources (Ontario)](https://www.cleo.on.ca/en/resources-and-publications/immigration-and-refugee) — Plain-language publications on refugee hearings, H&C applications, criminal charges and immigration status, and family sponsorship; available in multiple languages including Arabic, Somali, Tamil, Spanish, and Urdu.

### Journalism resources

- [Reporters Committee for Freedom of the Press — Immigration Reporting Legal Guide](https://www.rcfp.org/resources/immigration-reporting-legal-guide/) — US-focused but widely applicable guide covering FOIA requests to ICE, USCIS, and CBP; accessing immigration court hearings (presumptively open to the public); and rights when covering immigration enforcement. Updated November 2025.

### Notes on unsourced claims

The red flag on demeanour-based credibility findings is supported by a substantial body of Canadian Federal Court jurisprudence and academic literature, including the IRB's own guidance cited above. The Georgetown Immigration Law Journal (2022) and Cambridge University Press scholarship on refugee fact-finding also address this directly, though those sources are behind paywalls. The claim that boilerplate country-condition language is a ground of judicial review reflects settled Federal Court doctrine accessible through CanLII searches on "boilerplate" and "country conditions" in the Federal Court.
