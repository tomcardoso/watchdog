"""The demo vault's finalizer-stage content: what the canned model says when reconciliation,
entity synthesis, timeline de-duplication, request de-duplication and the briefing are asked.

Fictional throughout (see `demo_cast`). Per-document extraction content lives in `demo_docs_a` and
`demo_docs_b`; this module holds everything that needs the whole set of documents at once.
"""

from __future__ import annotations

# Candidate duplicate pairs the reconciler confirms: (id, id, id to keep, reason).
MERGES = [
    ("planning-and-procurement-committee", "planning-procurement-committee",
     "planning-and-procurement-committee",
     "Same standing committee of Council; the audit report writes 'and' as an ampersand."),
    ("port-calder-land-registry", "port-calder-land-registry-office", "port-calder-land-registry-office",
     "The same land registry; the audit report drops 'Office' from the name."),
]

# Contradictions the reconciler reports across documents: entity, label, then each side's value,
# document slug (the filename stem, as `write_vault._doc_slug` makes it) and page.
# `a_cite`/`b_cite` name a phrase of the fact each side comes from; the canned reconcile model
# returns that fact's short id (`a_fact`/`b_fact`), as the real one is asked to (D283).
CONTRADICTIONS = [
    dict(entity_id="lot-14-dockside-road", label="Purchase price of 14 Dockside Road",
         a_value="$3,900,000 (Report CR-2022-011, as approved by Council)", a_doc="council-minutes-2022-02-08",
         a_page=2, b_value="$4,350,000 (stated consideration, Instrument PC-447119)",
         b_doc="parcel-register-14-dockside-road", b_page=1,
         a_cite="Report CR-2022-011 recommended buying", b_cite="stated consideration of $4,350,000"),
    dict(entity_id="marcus-teague", label="Teague's authority on the transfer date",
         a_value="Resigned as director of 7714882 Holdings Ltd. effective January 15, 2022",
         a_doc="7714882-holdings-corporate-profile", a_page=2,
         b_value="Signed the February 28, 2022 transfer as Director of the vendor",
         b_doc="parcel-register-14-dockside-road", b_page=2,
         a_cite="records Marcus Teague's resignation", b_cite="executed for 7714882 Holdings Ltd. by Marcus Teague"),
    dict(entity_id="northgate-civil-works", label="Value of the Pier 9 contract",
         a_value="Not to exceed $48,600,000 (Council resolution)", a_doc="council-minutes-2022-04-26",
         a_page=2, b_value="Total Contract Price $52,340,000", b_doc="contract-c-2022-041-pier-9-servicing",
         b_page=2, a_cite="Report CR-2022-029 recommended awarding",
         b_cite="Total Contract Price is $52,340,000"),
    dict(entity_id="dana-whitcombe", label="Disclosure of the Whitcombe-Teague relationship",
         a_value="Says she disclosed it to the City Clerk in writing on April 11, 2022",
         a_doc="whitcombe-letter-to-integrity-commissioner", a_page=1,
         b_value="No declarations of pecuniary interest were made on April 26, 2022",
         b_doc="council-minutes-2022-04-26", b_page=1,
         a_cite="disclosed the relationship to the City Clerk",
         b_cite="No declarations of pecuniary interest were made at the April 26"),
    dict(entity_id="pier-9", label="Pier 9 berth permit expiry",
         a_value="Permits expire September 30, 2022 (staff report to Council)",
         a_doc="council-minutes-2022-04-26", a_page=2,
         b_value="Permits renewed March 3, 2022 for a term ending December 31, 2025",
         b_doc="harbour-authority-annual-report-2022", b_page=3,
         a_cite="berth permits expire on September 30, 2022", b_cite="renewed the Pier 9 berth permits"),
    dict(entity_id="city-of-port-calder", label="How the Pier 9 contract was procured",
         a_value="Awarded 'following a competitive process'", a_doc="news-release-pier-9-award", a_page=1,
         b_value="Not competitively procured; no justification on file",
         b_doc="city-auditor-procurement-review-ar-2023-04", b_page=2,
         a_cite="following a competitive process", b_cite="not competitively procured"),
]

# Entity synthesis: id -> (summary, analysis). Only entities that recur across documents get one.
SYNTHESIS = {
    "dana-whitcombe": (
        "Dana Whitcombe is the Ward 4 city councillor who chairs the Planning and Procurement Committee. She "
        "moved both Council motions at the centre of this investigation: the February 8, 2022 purchase of 14 "
        "Dockside Road and the April 26, 2022 award of the Pier 9 contract {{voted 8 to 3}}{{voted 7 to 3}}. Meridian's lobbyist reported three "
        "contacts with her between November 2021 and April 2022 {{Two of the three reported contacts}}, and she is the sister-in-law of Marcus "
        "Teague, the former director of the company that sold the City the property {{is her brother-in-law}}.",
        "The minutes of both meetings ([[documents/council-minutes-2022-02-08|February 8]], "
        "[[documents/council-minutes-2022-04-26|April 26]]) record no declaration of interest. Whitcombe's "
        "own [[documents/whitcombe-letter-to-integrity-commissioner|letter to the Integrity Commissioner]] "
        "says she disclosed the family relationship to the City Clerk on April 11, 2022 and was told it was "
        "on file {{because the Clerk advised her}}; the Clerk's record of that disclosure has not been seen. The "
        "[[documents/city-auditor-procurement-review-ar-2023-04|City Auditor]] flagged the absence of a "
        "recorded conflict, and the Integrity Commissioner's inquiry (IC-2022-07) is still open."),
    "marcus-teague": (
        "Marcus Teague was the first director and president of 7714882 Holdings Ltd., the numbered company "
        "that bought 14 Dockside Road in April 2021 and sold it to the City ten months later. The registry "
        "records his resignation effective January 15, 2022, when Meridian's chief executive replaced him "
        "{{records Marcus Teague's resignation}}. He "
        "is the brother-in-law of Councillor Dana Whitcombe.",
        "The [[documents/parcel-register-14-dockside-road|land title]] shows Teague signing the February 28, "
        "2022 transfer to the City as a director {{executed for 7714882 Holdings Ltd. by Marcus Teague}}, six weeks after the registry's "
        "[[documents/7714882-holdings-corporate-profile|Notice of Change]] says he resigned. Either the "
        "filing or the transfer is wrong about who could bind the company that day."),
    "7714882-holdings-ltd": (
        "7714882 Holdings Ltd. is a numbered company incorporated March 9, 2021. It bought 14 Dockside Road "
        "from the Estate of Albert Kessler for $1,150,000 {{transferred 14 Dockside Road to 7714882 Holdings Ltd. for $1,150,000}} "
        "and sold it to the City of Port Calder on February 28, 2022 for $4,350,000 {{stated consideration of $4,350,000}}{{City paid $4,350,000 to 7714882}}. In January 2022 its director changed from Marcus Teague to Meridian chief "
        "executive Tomasz Wieczorek and its registered office moved to Meridian's address.",
        "The company held the property for about ten months and resold it at roughly 3.8 times its cost, to a "
        "buyer whose price was nearly three times the City's own appraisal. The registry records no "
        "beneficial owner {{does not record the beneficial owners}}."),
    "lot-14-dockside-road": (
        "14 Dockside Road (Lot 14, Plan DP-2217) is the last privately held parcel on the Pier 9 approach. The "
        "Estate of Albert Kessler held it from 1987 until April 2021; 7714882 Holdings Ltd. owned it until the "
        "City bought it on February 28, 2022.",
        "Three prices circulate: $1,420,000 (the December 2021 appraisal {{market value of 14 Dockside Road at $1,420,000}}, released in "
        "[[documents/foi-response-lot-14-appraisal|January 2023]]), $3,900,000 (what "
        "[[documents/council-minutes-2022-02-08|Council approved]]) and $4,350,000 (the "
        "[[documents/parcel-register-14-dockside-road|land title]] and the City's own payment on March 4, "
        "2022). Council was told an appraisal supported its price {{described as the amount supported}}; the "
        "appraisal says otherwise {{not derived from the released appraisal}}."),
    "northgate-civil-works": (
        "Northgate Civil Works Ltd. is the Port Calder contractor that won the $48,600,000 Pier 9 marine "
        "servicing contract (C-2022-041) without a public tender {{no public call for tenders}}. It is a wholly owned subsidiary of "
        "Meridian Shoreline Developments Inc., shares Meridian's building at 410 Wharf Street, and was paid "
        "$23,120,000 in four progress payments by September 30, 2022 {{totalled $23,120,000 over four}}.",
        "The executed [[documents/contract-c-2022-041-pier-9-servicing|contract price]] is $52,340,000, which "
        "is $3,740,000 more than [[documents/council-minutes-2022-04-26|Council approved]]. A "
        "[[documents/citizens-v-city-reasons-for-judgment|court]] set the award aside as to unperformed work "
        "on November 30, 2023, and Northgate intervened in that case. Council's April 2022 vote was taken "
        "without being told of the Meridian link."),
    "meridian-shoreline-developments": (
        "Meridian Shoreline Developments Inc. is a Port Calder developer led by chief executive Tomasz "
        "Wieczorek. It has retained Strathmore Public Affairs to lobby the City since October 4, 2021 on the "
        "Harbourfront Lands, including the City's purchase of 14 Dockside Road and the Pier 9 servicing "
        "contract, and it owns Northgate Civil Works, the contractor that won that contract. Blackwater "
        "Capital Partners holds 35 percent of Meridian.",
        "Meridian's chief executive became director of 7714882 Holdings six weeks before it sold the City "
        "the property Meridian had [[documents/lobbyist-registration-lr-2021-0377|registered to lobby the "
        "City to buy]], and witnessed the [[documents/contract-c-2022-041-pier-9-servicing|Pier 9 contract]] "
        "for its subsidiary. The common thread is control of both sides of two City transactions."),
    "tomasz-wieczorek": (
        "Tomasz Wieczorek is chief executive officer of Meridian Shoreline Developments Inc. He became "
        "director and president of 7714882 Holdings Ltd. on January 15, 2022 and witnessed the Pier 9 "
        "contract for Northgate on May 17, 2022.",
        None),
    "strathmore-public-affairs": (
        "Strathmore Public Affairs Inc. is the lobbying firm registered (LR-2021-0377) to lobby the City for "
        "Meridian Shoreline Developments Inc. Its senior partner, Helen Okafor-Reyes, addressed Council in "
        "support of the Pier 9 award on April 26, 2022.",
        None),
    "helen-okafor-reyes": (
        "Helen Okafor-Reyes is the senior partner at Strathmore Public Affairs Inc. and Meridian's registered "
        "lobbyist. She reported meetings or calls with Councillor Whitcombe on November 17, 2021, February "
        "2, 2022 and April 12, 2022, met Procurement Director Leonard Pike on April 14, 2022, and spoke to "
        "Council on April 26, 2022.",
        "The first two contacts with Whitcombe bracket the February 8 vote on 14 Dockside Road; the third "
        "came a week before the committee recommended the Pier 9 award."),
    "leonard-pike": (
        "Leonard Pike is the City's Director of Procurement and Real Property. He authored the reports "
        "recommending the purchase of 14 Dockside Road and the sole-source award of the Pier 9 contract, and "
        "approved every payment in the 2022 capital register under delegated authority.",
        "Pike told Council that staff knew of no conflict affecting Northgate, and that the Pier 9 berth "
        "permits would expire in September 2022; the Harbour Authority had already renewed them to 2025. He "
        "met Meridian's lobbyist on April 14, 2022, twelve days before the vote. In his management response "
        "to the [[documents/city-auditor-procurement-review-ar-2023-04|auditor's report]] he disputes the "
        "findings on the procurement method and the property price."),
    "city-of-port-calder": (
        "The City of Port Calder bought 14 Dockside Road in February 2022 and awarded the Pier 9 marine "
        "servicing contract in April 2022. Its auditor, its Harbour Authority partner and a court have each "
        "documented departures from the procurement by-law in those two transactions.",
        "The City's [[documents/news-release-pier-9-award|May 2022 news release]] described the award as "
        "following a competitive process; the [[documents/city-auditor-procurement-review-ar-2023-04|auditor]] "
        "found it was not."),
    "port-calder-city-council": (
        "Port Calder City Council approved the purchase of 14 Dockside Road by an 8 to 3 vote on February 8, "
        "2022 and the Pier 9 award by 7 to 3 on April 26, 2022. In November 2023 a court ordered it to "
        "reconsider the award.",
        None),
    "planning-and-procurement-committee": (
        "The Planning and Procurement Committee is the standing committee of Council, chaired by Dana "
        "Whitcombe, that reviewed and recommended both the 14 Dockside Road purchase (January 31, 2022) and "
        "the Pier 9 award (April 19, 2022).",
        None),
    "pier-9": (
        "Pier 9 is the working berth on Calder Harbour whose approach channel, quay wall and underground "
        "servicing are the subject of Contract C-2022-041. The Harbour Authority holds the water lot and "
        "permits; the City manages the works.",
        "The urgency given for skipping a tender, berth permits expiring on September 30, 2022, does not "
        "survive the [[documents/harbour-authority-annual-report-2022|Harbour Authority's own record]] of a "
        "renewal to December 31, 2025."),
    "port-calder-harbour-authority": (
        "The Port Calder Harbour Authority operates Calder Harbour, declared the 38-acre Harbourfront Lands "
        "surplus in June 2019, issues the Pier 9 berth permits and contributed $6,200,000 to the Pier 9 "
        "shoreline works.",
        None),
    "tideway-marine-services": (
        "Tideway Marine Services Ltd. owns the Dredge Calder Princess and dredged the Pier 9 approach "
        "channel from June to November 2022 as Northgate's subcontractor. The City nonetheless paid it "
        "$1,640,000 directly on August 15, 2022.",
        "A direct City payment to a subcontractor is unexplained; neither the subcontract nor the invoice "
        "has been seen."),
    "harbourline-engineering": (
        "Harbourline Engineering Inc. is the City's Contract Administrator for the Pier 9 works, certifying "
        "Northgate's progress claims, and was paid $610,500 for Phase 2 design services in early 2022.",
        None),
    "macaskill-rowe-appraisals": (
        "Macaskill & Rowe Appraisals Ltd. prepared the only independent appraisal of 14 Dockside Road on file: "
        "$1,420,000 as of December 10, 2021, in a report marked for internal budgeting only. The City paid "
        "it $18,500 on January 31, 2022.",
        None),
    "anita-sandhu": (
        "Anita Sandhu is Port Calder's City Clerk and access coordinator. She signed the Pier 9 contract and "
        "represented the City on the 14 Dockside Road transfer, and issued the January 2023 decision "
        "releasing the appraisal. Councillor Whitcombe says she gave the Clerk a written disclosure on "
        "April 11, 2022.",
        None),
    "robert-delacroix": (
        "Robert Delacroix is the Mayor of Port Calder. He presided over both Council meetings, signed the "
        "Pier 9 contract and announced the award, calling it a landmark investment.",
        None),
    "410-wharf-street": (
        "410 Wharf Street is the Port Calder office address shared by Meridian Shoreline Developments "
        "(Suite 1200), Northgate Civil Works (Suite 1100) and, from January 2022, the registered office of "
        "7714882 Holdings Ltd.",
        "A shared address is the plainest documentary link among the three companies."),
    "harbourfront-lands": (
        "The Harbourfront Lands are 38 acres of former rail yard and bulk terminal that the Harbour "
        "Authority declared surplus in June 2019. Their redevelopment is the subject of Meridian's "
        "lobbying and of the City's 2022 capital program.",
        None),
    "calder-citizens-for-open-government": (
        "Calder Citizens for Open Government is a community group chaired by Samir Haddad. It complained to "
        "the Integrity Commissioner on July 8, 2022 and won a judicial review of the Pier 9 award in "
        "November 2023.",
        None),
    "integrity-commissioner-inquiry-ic-2022-07": (
        "Integrity Commissioner Inquiry IC-2022-07 examines the complaint, filed July 8, 2022, that "
        "Councillor Whitcombe had an undeclared conflict in the Pier 9 award. The Commissioner gave notice "
        "on July 20, 2022; Whitcombe answered by letter on August 2, 2022.",
        "The Superior Court expressly left the conflict question to this inquiry; no decision has been "
        "published."),
    "victor-anand": (
        "Victor Anand is a Port Calder city councillor from Ward 2. He seconded both motions at the centre of "
        "this investigation, the February 8, 2022 purchase of 14 Dockside Road and the April 26, 2022 Pier 9 "
        "award, each moved by Dana Whitcombe.",
        None),
    "nadia-ferreira": (
        "Nadia Ferreira is the Ward 1 councillor who voted against the 14 Dockside Road purchase and asked "
        "staff for a report on the City's real property disclosure practices. She was absent for the Pier 9 "
        "vote on April 26, 2022.",
        None),
    "ben-oyelaran": (
        "Ben Oyelaran is the Ward 6 councillor who twice asked questions staff could not satisfy: why the "
        "appraisal of 14 Dockside Road was not attached to the report, and whether the Pier 9 contractor was "
        "affiliated with parties lobbying the City. He voted against both motions.",
        None),
    "calder-harbour": (
        "Calder Harbour is the working port on which the Pier 9 works are being carried out. The Port Calder "
        "Harbour Authority operates it.",
        None),
    "port-calder-city-hall": (
        "Port Calder City Hall at 1 Civic Square houses Council Chamber, where the February 8 and April 26, "
        "2022 meetings were held, and the offices of the City Clerk and the Procurement and Real Property "
        "division.",
        None),
    "port-calder-land-registry-office": (
        "The Port Calder Land Registry Office keeps the parcel register for 14 Dockside Road. The City "
        "Auditor cites its record of the $4,350,000 sale price against the $3,900,000 in Council's minutes.",
        None),
    "dredge-calder-princess": (
        "The Dredge Calder Princess is a dredging vessel owned by Tideway Marine Services and used on the "
        "Pier 9 approach channel in 2022.",
        None),
    "ministry-of-municipal-affairs": (
        "The Provincial Ministry of Municipal Affairs contributed $9,000,000 to the Pier 9 programme in "
        "September 2022 under the Waterfront Infrastructure Program.",
        None),
    "office-of-the-integrity-commissioner": (
        "The Office of the Integrity Commissioner, led by Elaine Fortier, investigates complaints about "
        "councillors' conduct, including IC-2022-07, and told the City Auditor of the family link between "
        "Whitcombe and Teague.",
        None),
}

# Two briefings, one per ingest batch: the early run covers four documents from the City's side of
# the story; the main run adds the registry, lobbying, audit and court records that connect them.
BRIEFINGS = {
    "early": {
        "investigation_status": (
            "The first four documents show the City paying $29,739,000 on the Harbourfront program in nine "
            "months and a Pier 9 contract priced above what Council approved, with Meridian's name in the "
            "contract but not in the City's announcement."),
        "what_was_ingested": [
            "news-release-pier-9-award.pdf - News Release: the City announces the no-tender Pier 9 award as "
            "'competitive'",
            "capital-payment-register-2022.pdf - Payment Register: nine payments, $29,739,000, all approved by one "
            "official",
            "foi-response-lot-14-appraisal.pdf - Access to Information Response: the appraisal came in at $1,420,000",
            "contract-c-2022-041-pier-9-servicing.pdf - Procurement Contract: $52,340,000, Northgate owned by Meridian",
        ],
        "new_entities": [
            "Northgate Civil Works Ltd.", "Meridian Shoreline Developments Inc.", "City of Port Calder",
            "Leonard Pike", "Pier 9", "14 Dockside Road", "Tideway Marine Services Ltd.",
            "Macaskill & Rowe Appraisals Ltd.",
        ],
        "connections": [
            "Northgate Civil Works Ltd. is a wholly owned subsidiary of Meridian Shoreline Developments Inc. "
            "(contract p. 1), and Meridian's chief executive witnessed Northgate's signature.",
        ],
        "leads": [
            "Ask for the second valuation of 14 Dockside Road that the City says it cannot find.",
            "Ask why the City paid Tideway Marine Services directly when Tideway is Northgate's subcontractor.",
        ],
        "anomalies": [
            "The City paid $4,350,000 for 14 Dockside Road {{City paid $4,350,000 to 7714882}} against a $1,420,000 "
            "appraisal {{market value of 14 Dockside Road at $1,420,000}}.",
            "The contract price is $3,740,000 above the figure in the City's announcement "
            "{{Total Contract Price is $52,340,000}}.",
        ],
        "emerging_patterns": [
            "Every payment in the register was approved by the Director of Procurement and Real Property under "
            "delegated authority.",
        ],
        "open_questions": [
            "Who owns the company that sold the City 14 Dockside Road?",
            "Did Council know Northgate belonged to Meridian when it voted?",
        ],
    },
    "main": {
        "investigation_status": (
            "Fourteen documents now document two linked City transactions, the February 2022 purchase of 14 "
            "Dockside Road and the April 2022 Pier 9 contract, both steered by the same Meridian-connected "
            "circle and both found by the City Auditor and a court to have departed from the procurement "
            "by-law."),
        "what_was_ingested": [
            "council-minutes-2022-02-08.pdf - Council Minutes: the 8 to 3 vote to buy 14 Dockside Road for $3,900,000",
            "council-minutes-2022-04-26.pdf - Council Minutes: the 7 to 3 no-tender award of the Pier 9 contract",
            "7714882-holdings-corporate-profile.pdf - Corporate Registry Filing: Teague out, Meridian's CEO in",
            "parcel-register-14-dockside-road.pdf - Land Title Parcel Register: the $1,150,000 to $4,350,000 flip",
            "lobbyist-registration-lr-2021-0377.pdf - Lobbyist Registration: Strathmore for Meridian since "
            "October 2021",
            "harbour-authority-annual-report-2022.pdf - Annual Report: berth permits renewed to 2025",
            "news-release-pier-9-award-revised.pdf - News Release: near-duplicate of the May 2 release, name corrected",
            "city-auditor-procurement-review-ar-2023-04.pdf - Audit Report: five findings against the procurement",
            "whitcombe-letter-to-integrity-commissioner.png - Letter (scan): Whitcombe's account of her disclosure",
            "citizens-v-city-reasons-for-judgment.pdf - Court Judgment: award set aside as to unperformed work",
        ],
        "new_entities": [
            "Dana Whitcombe", "Marcus Teague", "Tomasz Wieczorek", "7714882 Holdings Ltd.",
            "Strathmore Public Affairs Inc.", "Helen Okafor-Reyes", "410 Wharf Street",
            "Calder Citizens for Open Government v. City of Port Calder",
        ],
        "connections": [
            "Meridian Shoreline Developments Inc. owns Northgate Civil Works Ltd., and its chief executive "
            "Tomasz Wieczorek replaced Marcus Teague as director of 7714882 Holdings Ltd. on January 15, 2022, "
            "so Meridian sat on both sides of the City's purchase and its Pier 9 award {{records Marcus Teague's resignation}}.",
            "Marcus Teague, 7714882's director until January 2022, is Councillor Dana Whitcombe's brother-in-law; "
            "Whitcombe chaired the committee and moved both motions {{is her brother-in-law}}{{voted 8 to 3}}{{voted 7 to 3}}.",
            "410 Wharf Street is the address of Meridian, Northgate and, from January 2022, 7714882 Holdings.",
        ],
        "leads": [
            "Get the City Clerk's record of the April 11, 2022 disclosure Whitcombe says she made - it would "
            "settle the contradiction between her letter and the April 26 minutes.",
            "Find out who owns 7714882 Holdings Ltd. The registry records no beneficial owner, but its address "
            "and director both moved to Meridian before the sale {{does not record the beneficial owners}}.",
            "Check whether the City filed a notice of appeal from the November 30, 2023 reasons.",
            "Ask for the Integrity Commissioner's notice of inquiry IC-2022-07 and any published decision.",
        ],
        "anomalies": [
            "The City paid 7714882 Holdings $4,350,000 on March 4, 2022, $450,000 more than the $3,900,000 "
            "Council approved {{could not reconcile the Land Registry}}.",
            "Teague signed the February 28, 2022 transfer as Director six weeks after his registered "
            "resignation {{executed for 7714882 Holdings Ltd. by Marcus Teague}}.",
            "The Pier 9 berth permits that staff said would expire on September 30, 2022 had been renewed to "
            "December 31, 2025 on March 3, 2022 {{berth permits expire on September 30, 2022}}"
            "{{renewed the Pier 9 berth permits}}.",
        ],
        "emerging_patterns": [
            "Both transactions went to Council on urgency arguments that later records undercut: a vendor "
            "'entertaining other offers' and berth permits that had already been renewed.",
            "Every figure Council saw was lower than the figure the City paid: $3,900,000 against $4,350,000, "
            "and $48,600,000 against $52,340,000.",
        ],
        "open_questions": [
            "Who benefited from the roughly $3,200,000 gap between what 7714882 paid for Lot 14 and what the "
            "City paid it?",
            "Did Councillor Whitcombe's disclosure reach the Clerk, and why do neither meeting's minutes record "
            "one?",
            "How much of the $52,340,000 has the City now paid, and is the unperformed work covered by the "
            "court's order?",
        ],
    },
}

# Timeline events that restate one another when they land on the same date: any two events on a date
# that each contain a phrase from the same group are the same fact. Identical text always matches.
DEDUP_GROUPS = [
    ["voted 7 to 3", "approved the award on april 26"],
    ["permits were renewed", "renewed the pier 9 berth permits"],
    ["4,350,000 for 14 dockside road", "transferred 14 dockside road to the city"],
    ["notice about the complaint", "opened inquiry ic-2022-07"],
]

# A month-dated event restating a day-dated one (coarse phrase, precise phrase).
PRECISION_PAIRS = [("begin in june 2022", "mobilization under the contract begins")]

# Document requests that ask for the same thing in different words.
REQUEST_GROUPS = [
    ["subcontract between northgate", "northgate's subcontract"],
    ["competitive-process"],
]

# Relationship wordings that name the same relationship (D291), as a careful model would group them:
# the judgment calls Marisol Fernandes counsel for the citizens' group, the councillor's letter its
# lawyer. The canned model keeps every other pair holding two wordings in the story apart (a
# director and a president, a subsidiary and an affiliate, an owner and an operator, a contract
# award and a payment); a partner and a senior partner, which the prompt now lets a model group,
# are also left apart here so the demo shows one grouping.
RELATIONSHIP_GROUPS = [("counsel for", "lawyer for")]
