# Domain knowledge — Criminal proceedings

This skill is loaded by `/ingest` when the document type is a criminal court document — including a charging document, bail or remand decision, preliminary hearing transcript, trial decision, sentencing decision, or appeal in a criminal matter.

This skill covers criminal proceedings specifically. For non-criminal court documents (civil claims, injunctions, administrative tribunal decisions), see the `court-documents` skill. For police occurrence reports and disciplinary records, see the `police-records` skill.

Apply this knowledge in addition to the standard extraction process. It tells you what to look for, what terminology means, and what patterns are worth flagging.

---

## Document types covered

- Charging documents (information, indictment, complaint, charge sheet)
- Bail / remand / judicial interim release decisions and conditions
- Preliminary hearing transcripts and decisions
- Trial decisions (judge alone or jury)
- Sentencing decisions and pre-sentence reports
- Conditional release and supervision orders
- Dangerous or high-risk offender designations
- Appeals (conviction, acquittal, sentence)
- Search warrant applications and wiretap authorizations (where unsealed)
- Mutual legal assistance treaty (MLAT) records
- Proceeds of crime restraint and forfeiture orders
- In Canada: informations, indictments, CCAA s.11(b) applications, Gladue reports, dangerous offender designations
- In the US: indictments, plea agreements, federal sentencing guidelines calculations
- In the UK: charge sheets, Crown Court indictments, Newton hearings

---

## Always-present fields to extract

| Field | What to look for |
|-------|-----------------|
| **Accused / defendant name** | Full legal name as stated in the charging document |
| **Prosecutor** | The prosecuting authority (public prosecutor, crown attorney, district attorney) |
| **Defence counsel** | Lawyer representing the accused |
| **Court and location** | Which court and jurisdiction |
| **Judge** | Presiding judge's name |
| **Charges** | Offence description and statutory section(s) |
| **Date of alleged offence** | When the alleged crime occurred |
| **Plea** | Guilty or not guilty |
| **Verdict** | Guilty, not guilty, acquitted, stayed, dismissed |
| **Sentence** | Imprisonment, conditional sentence, fine, probation, discharge |
| **Bail conditions** | Conditions imposed while awaiting trial |
| **Co-accused** | Other persons charged in the same matter |
| **Victims / complainants** | Identity (often protected by publication ban or non-publication order) |

---

## Red flags — what to look for

### Charges and prosecutorial decisions

- **Stayed or withdrawn charges** — charges stayed or withdrawn after significant investigative effort may indicate prosecutorial problems (evidence issues, prosecutor error) or witness failures. Note the stage at which charges were dropped.
- **Delay-based stay of proceedings** — a stay because the accused was not tried within a reasonable time. This means the case was lost because the system was too slow, not because of innocence.
- **Charge reduction before trial** — a serious charge reduced to a lesser offence before trial may indicate the prosecution's case weakened.
- **Multiple charges stayed while others proceed** — selective prosecution of a subset of charges may indicate the full indictment was not provable.
- **No-charge recommendation not followed** — where investigators made a no-charge recommendation but the prosecutor proceeded anyway (or vice versa), the divergence is significant.

### Bail and remand

- **Reverse onus** — for certain serious offences, the onus shifts to the accused to show cause why detention is not justified. Note whether reverse onus applied and whether it was discharged.
- **Surety or bailor connected to the offence** — the person vouching for the accused has a business or personal connection to the alleged victim or co-accused.
- **Conditions imposing geographic or contact restrictions** — bail conditions that prohibit contact with specific people or attendance in specific places (relevant where the accused has a business or public role).
- **Electronic monitoring conditions** — note whether ankle monitoring was imposed and whether it was complied with.

### Trial and evidence

- **Evidence excluded by a charter, constitutional, or human rights ruling** — evidence excluded because of a rights violation is often the most important evidence in the case. The exclusion ruling explains what was found and what it produced. Even if excluded from the trial, the underlying facts may be newsworthy.
- **Credibility findings** — a trial judge's credibility assessment of witnesses, including police officers, is a matter of public record. A finding that an officer was not credible is significant.
- **Expert evidence disputes** — where competing experts testified, note the subject matter and which expert the court preferred and why.

### Sentencing

- **Pre-sentence report contents** — the pre-sentence report contains a detailed background on the offender's history, circumstances, and risk assessment. It is often the most comprehensive public document about an individual's background.
- **Victim impact statements** — read into the record at sentencing; public and often contain significant detail about the impact of the offence.
- **Dangerous or high-risk offender designation** — an indeterminate or extended sentence imposed on an offender found to pose a pattern threat to public safety. Note the predicate offence and the pattern of prior conduct that justified the designation.
- **Proceeds of crime forfeiture** — an order forfeiting property to the state because it was proceeds or instruments of crime. The list of forfeited property and its value are public.

---

## Jurisdiction terminology

| Term | Jurisdiction | Meaning |
|------|-------------|---------|
| **Information** | Canada | The charging document for summary and hybrid offences — sworn by a police officer before a justice of the peace |
| **Indictment** | Canada / US / UK | The formal charging document for serious offences |
| **Summary conviction offence** | Canada | Less serious offence tried in provincial court without a jury |
| **Indictable offence** | Canada | Serious offence; accused may elect trial by judge alone or judge and jury |
| **Hybrid offence** | Canada | An offence where the Crown elects to proceed summarily or by indictment |
| **Preliminary inquiry** | Canada | A pre-trial hearing to determine if there is sufficient evidence to commit the accused to trial |
| **Voir dire** | Canada / UK | A "trial within a trial" — a hearing on the admissibility of evidence |
| **Stay of proceedings** | Canada / UK | A halt to the prosecution |
| **Conditional sentence** | Canada | A jail sentence served in the community under strict conditions |
| **Gladue report** | Canada | A specialized pre-sentence report for Indigenous offenders examining systemic factors |
| **s.11(b)** | Canada | Section 11(b) of the Charter — the right to be tried within a reasonable time |
| **Publication ban / non-publication order** | Canada / UK / Australia | A court order restricting publication of certain information |
| **Plea agreement / plea deal** | US / Canada | A negotiated guilty plea in exchange for a reduced charge or sentence recommendation |
| **Grand jury indictment** | US | The formal charging instrument in federal and many state serious criminal matters |
| **Misdemeanor / felony** | US | The US equivalent of summary and indictable offences respectively |
| **Newton hearing** | UK | A hearing to determine disputed facts relevant to sentencing after a guilty plea |
| **Confiscation order** | UK | The equivalent of a proceeds of crime forfeiture order |

---

## Relationships to extract from criminal proceedings

1. **Person → Charge**: Accused and each count (offence, section, date of alleged offence)
2. **Person → Co-accused**: Others charged in the same matter
3. **Person → Counsel**: Defence lawyer and prosecuting attorney
4. **Person → Court**: Where and before whom the matter was heard
5. **Person → Outcome**: Verdict and sentence (with date)
6. **Person → Victim**: Alleged victim (note any publication ban before identifying)
7. **Property → Forfeiture order**: Assets subject to proceeds of crime order

---

## What investigators typically miss

1. **Publication bans and non-publication orders** — before publishing names or details, check whether any restriction is in effect. Bans covering victims' identities, bail hearing evidence, preliminary inquiry evidence, and youth matters are common in many jurisdictions. Violating such an order can be a criminal offence.
2. **The charging document** — the charging document is the most precise statement of what the prosecution alleges. It lists every count, the specific statutory section, and the date range of the alleged conduct. Always read it before reading anything else.
3. **Wiretap or surveillance authorizations** — where a prosecution arose from a wiretap or electronic surveillance investigation, the authorization itself (and the supporting affidavit) may be unsealed after the case concludes. These documents describe the scope and targets of the surveillance.
4. **Asset restraint orders** — in proceeds-of-crime cases, property may be restrained before conviction and forfeited after. The restraint order describes the property and the alleged connection to crime; it is public when granted.
5. **The sentencing judge's proportionality analysis** — a sentencing decision explains why a particular sentence is proportionate to the offence and offender. A sentence that departs significantly from the range (upward or downward) is a story.
6. **Co-accused cooperation** — where one co-accused pleaded guilty and received a reduced sentence in exchange for cooperation, the sentencing decision for the cooperating witness often reveals the substance of their assistance and what they admitted.

---

## Sources and further reading

### Official and regulatory

- [CanLII — Canadian Legal Information Institute](https://www.canlii.org/) — Free, searchable database of Canadian court decisions, tribunal rulings, statutes, and regulations from all jurisdictions; the primary public source for Canadian case law including criminal decisions.
- [BAILII — British and Irish Legal Information Institute](https://www.bailii.org/) — Free, password-free database of court decisions and legislation from the UK, Ireland, and related jurisdictions; includes Crown Court and Court of Appeal criminal decisions.
- [Charterpedia: Section 11(b) — Trial within a reasonable time](https://www.justice.gc.ca/eng/csj-sjc/rfc-dlc/ccrf-ccdl/check/art11b.html) — Department of Justice Canada's annotated guide to the right to be tried within a reasonable time, covering the R v Jordan framework and relevant case law.
- [R v Jordan, 2016 SCC 27 (CanLII)](https://www.canlii.org/en/ca/scc/doc/2016/2016scc27/2016scc27.html) — The Supreme Court of Canada decision that established the 18- and 30-month presumptive ceilings for trial delay under s.11(b) of the Charter; the governing authority on delay-based stays.
- [Spotlight on Gladue — Department of Justice Canada](https://www.justice.gc.ca/eng/rp-pr/jr/gladue/p1.html) — Federal research report examining how courts apply Gladue principles in sentencing Indigenous offenders and the persistent overrepresentation of Indigenous people in the criminal justice system.

### Practitioner and public interest

- [CourtListener — Free Law Project](https://www.courtlistener.com/) — Free, searchable archive of US federal and state court opinions, oral argument recordings, and PACER filings; includes over 10 million legal opinions and 17 million documents.
- [Criminal Law Notebook](https://criminalnotebook.ca/index.php/Main_Page) — Free Canadian criminal law reference covering Criminal Code offences, procedure, and case law; practical guide to the rules governing charging, bail, trial, and sentencing.

### Journalism resources

- [Reporters Committee for Freedom of the Press — Access to Criminal Court Records](https://www.rcfp.org/open-court-sections/a-in-general-iv-access-to-criminal-court-records/) — Comprehensive guide to the constitutional, common-law, and statutory rights of public access to criminal court records in every US jurisdiction.
- [Reporters Committee — Open Government Guide](https://www.rcfp.org/open-government-guide/) — State-by-state compendium of US open records and open meetings laws, including rules governing access to criminal investigation and court records.
