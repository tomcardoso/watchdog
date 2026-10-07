"""Demo documents 8-14: a harbour authority annual report, a news release and its revised
re-issue (the near-duplicate pair), a payment ledger, a city auditor's report, a scanned letter
and a court judgment. Fictional throughout; see `demo_cast` and `demo_docs_a`."""

import copy

from watchdog.gui.demo_cast import F, R, REQ

DOCS_B = []

# ── 8. Harbour Authority annual report ──────────────────────────────────────────────────────
DOCS_B.append(dict(
    file="harbour-authority-annual-report-2022.pdf", kind="pdf",
    title="Port Calder Harbour Authority - 2022 Annual Report", dtype="Annual Report",
    date="2023-03-30", skill="government-reports.md",
    source="Port Calder Harbour Authority website", obtained="2024-03-11",
    author="Port Calder Harbour Authority", footer="Harbour Authority Annual Report 2022  -  Page {n} of {total}",
    morgue="port-calder-harbour-authority",
    summary=("The Port Calder Harbour Authority's 2022 annual report. It recounts the 2019 decision to declare "
             "the 38-acre Harbourfront Lands surplus, records a $6,200,000 Authority contribution to Pier 9 "
             "shoreline works and a $9,000,000 provincial grant, reports that the Pier 9 berth permits were "
             "renewed on March 3, 2022 through December 31, 2025, and says Tideway Marine Services dredged the "
             "Pier 9 approach with the Dredge Calder Princess from June to November 2022."),
    scratch=("- Berth permits renewed Mar 3, 2022 to Dec 31, 2025 - contradicts the 'expire Sept 30, 2022' "
             "urgency given to Council.\n- Tideway dredged with its own dredge; Authority paid nothing to Tideway.\n"
             "- Province put in $9,000,000 (Waterfront Infrastructure Program)."),
    pages=[
        [("letterhead", "Port Calder Harbour Authority", "Annual Report 2022  |  Calder Harbour"),
         ("title", "Annual Report 2022"),
         ("h", "Message from the Chief Executive Officer"),
         ("p", "Grace Liu, Chief Executive Officer of the Port Calder Harbour Authority, writes that 2022 was "
               "the year the Harbourfront Lands moved from planning to construction. Cargo volumes at Calder "
               "Harbour recovered to 91 percent of the 2019 level and the Authority closed the year with a "
               "surplus of $3,420,000."),
         ("p", "The Authority thanks its partners in the City of Port Calder and the Provincial Ministry of "
               "Municipal Affairs for their support of the Pier 9 programme.")],
        [("h", "The Harbourfront Lands"),
         ("p", "On June 18, 2019 the Board of the Authority declared the 38-acre Harbourfront Lands surplus to "
               "port needs and transferred stewardship of the site to the City of Port Calder. The lands had "
               "served as a rail yard and bulk terminal for more than a century."),
         ("p", "The Authority retained the water lot at Pier 9 and the right to approve any marine works "
               "affecting the Pier 9 approach channel.")],
        [("h", "Pier 9 Marine Servicing"),
         ("p", "In 2022 the Authority contributed $6,200,000 toward shoreline stabilization at Pier 9. The "
               "Authority renewed the Pier 9 berth permits on March 3, 2022 for a term ending December 31, "
               "2025."),
         ("p", "Tideway Marine Services Ltd. dredged the Pier 9 approach channel from June to November 2022 "
               "using the Dredge Calder Princess, a vessel it owns. The Authority is not a party to the "
               "dredging subcontract and made no payments to Tideway."),
         ("p", "The Authority's role is limited to permits and navigation safety; contract management for the "
               "Pier 9 works rests with the City.")],
        [("h", "Financial Summary"),
         ("table", [["Item", "2022", "2021"], ["Operating revenue", "$41,380,000", "$38,910,000"],
                    ["Operating expenses", "$37,960,000", "$36,240,000"], ["Surplus for the year", "$3,420,000",
                    "$2,670,000"], ["Capital contributions", "$6,200,000", "$1,100,000"]], [200, 134, 134]),
         ("p", "Operating revenue in 2022 was $41,380,000, an increase of 6.3 percent over 2021.")],
        [("h", "Funding and Governance"),
         ("p", "In September 2022 the Provincial Ministry of Municipal Affairs contributed $9,000,000 to the "
               "Pier 9 programme under the Waterfront Infrastructure Program."),
         ("p", "The Board met eight times in 2022. No director declared a conflict of interest in a matter "
               "before the Board.")],
    ],
    ents=["port-calder-harbour-authority", "grace-liu", "calder-harbour", "harbourfront-lands",
          "city-of-port-calder", "ministry-of-municipal-affairs", "pier-9", "tideway-marine-services",
          "dredge-calder-princess"],
    facts=[
        F(1, "Grace Liu is Chief Executive Officer of the Port Calder Harbour Authority, which closed 2022 with "
             "a $3,420,000 surplus.", ["grace-liu", "port-calder-harbour-authority"],
          q="Grace Liu, Chief Executive Officer of the Port Calder Harbour Authority"),
        F(2, "On June 18, 2019 the Authority's Board declared the 38-acre Harbourfront Lands surplus and "
             "transferred stewardship to the City of Port Calder.",
          ["port-calder-harbour-authority", "harbourfront-lands", "city-of-port-calder"],
          q="On June 18, 2019 the Board of the Authority declared", date="2019-06-18"),
        F(2, "The Authority kept the Pier 9 water lot and the right to approve marine works on the Pier 9 "
             "approach channel.", ["port-calder-harbour-authority", "pier-9"],
          q="The Authority retained the water lot at Pier 9"),
        F(3, "The Authority contributed $6,200,000 in 2022 toward shoreline stabilization at Pier 9.",
          ["port-calder-harbour-authority", "pier-9"], q="In 2022 the Authority contributed $6,200,000",
          date="2022"),
        F(3, "The Authority renewed the Pier 9 berth permits on March 3, 2022 for a term ending December 31, "
             "2025.", ["port-calder-harbour-authority", "pier-9"],
          q="The Authority renewed the Pier 9 berth permits on March 3, 2022", date="2022-03-03"),
        F(3, "Tideway Marine Services dredged the Pier 9 approach from June to November 2022 with the Dredge "
             "Calder Princess, a vessel it owns; the Authority made no payments to Tideway.",
          ["tideway-marine-services", "dredge-calder-princess", "pier-9", "port-calder-harbour-authority"],
          q="Tideway Marine Services Ltd. dredged the Pier 9 approach channel", date="2022-06"),
        F(4, "Operating revenue in 2022 was $41,380,000, up 6.3 percent from 2021.",
          ["port-calder-harbour-authority"], q="Operating revenue in 2022 was $41,380,000", date="2022"),
        F(5, "In September 2022 the Provincial Ministry of Municipal Affairs contributed $9,000,000 to the "
             "Pier 9 programme under the Waterfront Infrastructure Program.",
          ["ministry-of-municipal-affairs", "pier-9", "port-calder-harbour-authority"],
          q="In September 2022 the Provincial Ministry of Municipal Affairs contributed $9,000,000",
          date="2022-09"),
    ],
    roles=[
        R("grace-liu", "Chief Executive Officer of", "port-calder-harbour-authority", 1),
        R("port-calder-harbour-authority", "Operates", "calder-harbour", 1),
        R("port-calder-harbour-authority", "Declared surplus", "harbourfront-lands", 2, "2019-06-18"),
        R("port-calder-harbour-authority", "Funder of", "pier-9", 3, "2022"),
        R("ministry-of-municipal-affairs", "Funder of", "pier-9", 5, "2022-09"),
        R("tideway-marine-services", "Owner of", "dredge-calder-princess", 3),
    ],
    requests=[REQ("Permit record", "Pier 9 berth permit renewal issued by the Harbour Authority on March 3, 2022",
                  "Fixes the permit term that staff gave Council as the reason for skipping a tender",
                  "Port Calder Harbour Authority")],
))

# ── 9 & 10. News release and its revised re-issue (the near-duplicate pair) ─────────────────
_RELEASE_PAGES = [
    [("letterhead", "City of Port Calder", "News Release  |  Office of the Mayor"),
     ("small", "FOR IMMEDIATE RELEASE  -  May 2, 2022"),
     ("title", "City awards $48,600,000 Pier 9 servicing contract to local firm"),
     ("p", "PORT CALDER - The City of Port Calder has awarded Contract C-2022-041 for marine servicing works "
           "at Pier 9 to Northgate Civil Works Ltd., Mayor Robert Delacroix announced today following a "
           "competitive process."),
     ("p", "'This is a landmark investment in our working waterfront,' Mayor Delacroix said. 'Northgate is a "
           "Port Calder company and the jobs will stay in Port Calder.'"),
     ("p", "Council approved the award on April 26, 2022 at a value not to exceed $48,600,000. Work on Pier 9 "
           "is scheduled to begin in June 2022 and to be substantially complete by fall 2024.")],
    [("h", "About the project"),
     ("p", "The Pier 9 works include dredging, quay-wall reconstruction and underground servicing for the "
           "Harbourfront Lands. The Provincial Ministry of Municipal Affairs and the Port Calder Harbour "
           "Authority are funding partners."),
     ("p", "'Marine servicing at Pier 9 is time-critical,' said Leonard Pike, Director of Procurement and "
           "Real Property. 'The permits and the construction window left no room for delay.'"),
     ("h", "Media contact"),
     ("p", "Office of the Mayor, communications@portcalder.example, 555-0142.")],
]
_RELEASE_FACTS = [
    F(1, "The City announced on May 2, 2022 that it awarded Contract C-2022-041 to Northgate Civil Works Ltd. "
         "following a competitive process.", ["city-of-port-calder", "northgate-civil-works", "robert-delacroix"],
      q="PORT CALDER - The City of Port Calder has awarded Contract C-2022-041", date="2022-05-02"),
    F(1, "Mayor Delacroix called the award a landmark investment and said Northgate is a Port Calder company "
         "whose jobs will stay in Port Calder.", ["robert-delacroix", "northgate-civil-works"],
      q="This is a landmark investment in our working waterfront"),
    F(1, "Council approved the award on April 26, 2022 at a value not to exceed $48,600,000.",
      ["port-calder-city-council", "northgate-civil-works"],
      q="Council approved the award on April 26, 2022", date="2022-04-26"),
    F(1, "Work on Pier 9 was scheduled to begin in June 2022 and finish by fall 2024.",
      ["pier-9", "northgate-civil-works"], q="Work on Pier 9 is scheduled to begin in June 2022", date="2022-06"),
    F(2, "The Provincial Ministry of Municipal Affairs and the Port Calder Harbour Authority are funding "
         "partners in the Pier 9 works.", ["ministry-of-municipal-affairs", "port-calder-harbour-authority", "pier-9"],
      q="The Provincial Ministry of Municipal Affairs and the Port Calder Harbour Authority are funding partners"),
    F(2, "Leonard Pike said marine servicing at Pier 9 is time-critical and that permits and the construction "
         "window left no room for delay.", ["leonard-pike", "pier-9"],
      q="Marine servicing at Pier 9 is time-critical"),
]
_RELEASE_ROLES = [
    R("robert-delacroix", "Mayor of", "city-of-port-calder", 1),
    R("leonard-pike", "Director of Procurement and Real Property at", "city-of-port-calder", 2),
    R("city-of-port-calder", "Awarded contract to", "northgate-civil-works", 1, "2022-05-02"),
]
_RELEASE_ENTS = ["city-of-port-calder", "robert-delacroix", "northgate-civil-works", "port-calder-city-council",
                 "pier-9", "harbourfront-lands", "ministry-of-municipal-affairs", "port-calder-harbour-authority",
                 "leonard-pike"]

DOCS_B.append(dict(
    file="news-release-pier-9-award.pdf", kind="pdf",
    title="City awards $48,600,000 Pier 9 servicing contract to local firm", dtype="News Release",
    date="2022-05-02", skill="news-clippings.md", source="City of Port Calder media centre", obtained="2022-05-02",
    author="Office of the Mayor", footer="City of Port Calder news release, May 2, 2022  -  Page {n} of {total}",
    morgue="city-of-port-calder",
    summary=("A City of Port Calder news release of May 2, 2022 announcing the award of Contract C-2022-041 "
             "to Northgate Civil Works Ltd. 'following a competitive process', quoting Mayor Delacroix and "
             "Leonard Pike, and naming the Province and Harbour Authority as funding partners. The release "
             "spells Pike's name 'Pyke'."),
    scratch=("- Release says 'competitive process'; the minutes record a sole-source under the time-critical "
             "exception.\n- Release does not mention Northgate's parent, Meridian.\n- 'Pyke' typo in the Pike quote."),
    pages=copy.deepcopy(_RELEASE_PAGES), ents=_RELEASE_ENTS, facts=_RELEASE_FACTS, roles=_RELEASE_ROLES,
    requests=[REQ("Procurement file", "Bid evaluation or competitive-process record for Contract C-2022-041",
                  "The release says the award followed a competitive process; the minutes describe none",
                  "City Procurement and Real Property")],
    typo=("Leonard Pike", "Leonard Pyke"),
))

_revised = copy.deepcopy(_RELEASE_PAGES)
_revised[0][1] = ("small", "FOR IMMEDIATE RELEASE  -  May 2, 2022. REVISED May 3, 2022 to correct the "
                           "spelling of a name in the second page.")
DOCS_B.append(dict(
    file="news-release-pier-9-award-revised.pdf", kind="pdf",
    title="City awards $48,600,000 Pier 9 servicing contract to local firm (revised)", dtype="News Release",
    date="2022-05-03", skill="news-clippings.md", source="City of Port Calder media centre", obtained="2022-05-03",
    author="Office of the Mayor", footer="City of Port Calder news release, May 2, 2022  -  Page {n} of {total}",
    morgue="city-of-port-calder",
    summary=("The revised re-issue of the City's May 2, 2022 news release on the Pier 9 award, corrected on "
             "May 3, 2022 to fix the spelling of Leonard Pike's name. The text is otherwise the same, "
             "including the 'competitive process' claim."),
    scratch="- Near-identical to the May 2 release; only the dateline note and one name changed.",
    pages=_revised, ents=_RELEASE_ENTS, facts=copy.deepcopy(_RELEASE_FACTS), roles=_RELEASE_ROLES,
    requests=[REQ("Procurement record", "The competitive-process evaluation the City cites for the Pier 9 award "
                  "(Contract C-2022-041)", "Would show whether any other bidder was considered",
                  "City of Port Calder")],
))

# ── 11. Capital payment register ────────────────────────────────────────────────────────────
DOCS_B.append(dict(
    file="capital-payment-register-2022.pdf", kind="pdf",
    title="Capital Projects Payment Register - Harbourfront Program, January to September 2022",
    dtype="Payment Register", date="2022-10-14", skill="financial-statements.md",
    source="City of Port Calder, released under access request AR-2022-198", obtained="2022-12-02",
    author="City of Port Calder Finance", footer="Payment register AR-2022-198  -  Page {n} of {total}",
    morgue="city-of-port-calder",
    summary=("A City of Port Calder register of Harbourfront Program capital payments from January to "
             "September 2022: $4,350,000 to 7714882 Holdings Ltd. on March 4, four progress payments to "
             "Northgate Civil Works Ltd. totalling $23,120,000, and a direct $1,640,000 payment to Tideway "
             "Marine Services Ltd. Every payment was approved under the Procurement Director's delegated "
             "authority. Total payments: $29,739,000."),
    scratch=("- Paid 7714882 $4,350,000 on Mar 4 - matches the title, not the $3,900,000 Council approved.\n"
             "- Tideway paid directly ($1,640,000) although it is Northgate's subcontractor.\n"
             "- Northgate progress payments total $23,120,000 by Sept 30, 2022."),
    pages=[
        [("letterhead", "City of Port Calder", "Finance Department  |  Capital Projects Payment Register"),
         ("title", "Harbourfront Program - Payments, Q1 2022"),
         ("small", "Released under access request AR-2022-198. Amounts in Canadian dollars."),
         ("table", [["Date", "Payee", "Description", "Amount", "Approved"],
                    ["2022-01-14", "Harbourline Engineering Inc.", "Design services, Phase 2", "$312,500", "L. Pike"],
                    ["2022-01-31", "Macaskill & Rowe Appraisals Ltd.", "Appraisal, 14 Dockside Road", "$18,500",
                     "L. Pike"],
                    ["2022-03-04", "7714882 Holdings Ltd.", "Land acquisition, 14 Dockside Road", "$4,350,000",
                     "L. Pike"],
                    ["2022-03-31", "Harbourline Engineering Inc.", "Design services, Phase 2", "$298,000",
                     "L. Pike"]], [66, 138, 148, 66, 50]),
         ("p", "On March 4, 2022 the City paid $4,350,000 to 7714882 Holdings Ltd. for the acquisition of 14 "
               "Dockside Road."),
         ("p", "On January 31, 2022 the City paid $18,500 to Macaskill & Rowe Appraisals Ltd. for the "
               "appraisal of 14 Dockside Road.")],
        [("title", "Harbourfront Program - Payments, Q2 and Q3 2022"),
         ("table", [["Date", "Payee", "Description", "Amount", "Approved"],
                    ["2022-06-30", "Northgate Civil Works Ltd.", "Pier 9 progress payment 1", "$4,125,000",
                     "L. Pike"],
                    ["2022-07-29", "Northgate Civil Works Ltd.", "Pier 9 progress payment 2", "$5,310,000",
                     "L. Pike"],
                    ["2022-08-15", "Tideway Marine Services Ltd.", "Dredging, Pier 9 approach", "$1,640,000",
                     "L. Pike"],
                    ["2022-08-31", "Northgate Civil Works Ltd.", "Pier 9 progress payment 3", "$6,480,000",
                     "L. Pike"],
                    ["2022-09-30", "Northgate Civil Works Ltd.", "Pier 9 progress payment 4", "$7,205,000",
                     "L. Pike"]], [66, 138, 148, 66, 50]),
         ("p", "On August 15, 2022 the City paid $1,640,000 directly to Tideway Marine Services Ltd. for "
               "dredging the Pier 9 approach."),
         ("p", "Progress payments to Northgate Civil Works Ltd. are certified by Harbourline Engineering Inc. "
               "as Contract Administrator.")],
        [("title", "Summary by Payee"),
         ("table", [["Payee", "Payments", "Total"], ["Northgate Civil Works Ltd.", "4", "$23,120,000"],
                    ["7714882 Holdings Ltd.", "1", "$4,350,000"], ["Tideway Marine Services Ltd.", "1",
                    "$1,640,000"], ["Harbourline Engineering Inc.", "2", "$610,500"],
                    ["Macaskill & Rowe Appraisals Ltd.", "1", "$18,500"], ["All payees", "9", "$29,739,000"]],
          [240, 100, 128]),
         ("p", "All payments in this register were approved under the delegated authority of the Director of "
               "Procurement and Real Property. No payment in this register was reported to Council as a "
               "separate item.")],
    ],
    ents=["city-of-port-calder", "7714882-holdings-ltd", "lot-14-dockside-road", "northgate-civil-works", "pier-9",
          "tideway-marine-services", "harbourline-engineering", "macaskill-rowe-appraisals", "leonard-pike"],
    facts=[
        F(1, "On March 4, 2022 the City paid $4,350,000 to 7714882 Holdings Ltd. for the acquisition of 14 "
             "Dockside Road.", ["city-of-port-calder", "7714882-holdings-ltd", "lot-14-dockside-road", "leonard-pike"],
          q="On March 4, 2022 the City paid $4,350,000 to 7714882 Holdings Ltd.", date="2022-03-04"),
        F(1, "On January 31, 2022 the City paid Macaskill & Rowe Appraisals Ltd. $18,500 for the appraisal of "
             "14 Dockside Road.", ["city-of-port-calder", "macaskill-rowe-appraisals", "lot-14-dockside-road"],
          q="On January 31, 2022 the City paid $18,500", date="2022-01-31"),
        F(2, "The City paid Northgate Civil Works Ltd. a first Pier 9 progress payment of $4,125,000 on "
             "June 30, 2022.", ["city-of-port-calder", "northgate-civil-works", "pier-9"],
          q="2022-06-30 Northgate Civil Works Ltd. Pier 9 progress payment 1", date="2022-06-30"),
        F(2, "On August 15, 2022 the City paid $1,640,000 directly to Tideway Marine Services Ltd. for "
             "dredging the Pier 9 approach.", ["city-of-port-calder", "tideway-marine-services", "pier-9"],
          q="On August 15, 2022 the City paid $1,640,000 directly to Tideway", date="2022-08-15"),
        F(2, "The City paid Northgate a fourth Pier 9 progress payment of $7,205,000 on September 30, 2022.",
          ["city-of-port-calder", "northgate-civil-works", "pier-9"],
          q="2022-09-30 Northgate Civil Works Ltd. Pier 9 progress payment 4", date="2022-09-30"),
        F(2, "Harbourline Engineering Inc. certifies Northgate's progress payments as Contract Administrator.",
          ["harbourline-engineering", "northgate-civil-works"],
          q="Progress payments to Northgate Civil Works Ltd. are certified by Harbourline Engineering"),
        F(3, "Payments to Northgate Civil Works Ltd. from January to September 2022 totalled $23,120,000 "
             "over four progress payments.", ["northgate-civil-works", "city-of-port-calder"],
          q="Northgate Civil Works Ltd. 4 $23,120,000"),
        F(3, "Every payment in the register was approved under the Procurement Director's delegated authority "
             "and none was reported to Council as a separate item.", ["leonard-pike", "city-of-port-calder"],
          q="All payments in this register were approved under the delegated authority"),
        F(3, "Total payments in the register came to $29,739,000.", ["city-of-port-calder"],
          q="All payees 9 $29,739,000"),
    ],
    roles=[
        R("city-of-port-calder", "Paid", "northgate-civil-works", 2, "2022-06-30 to 2022-09-30"),
        R("city-of-port-calder", "Paid", "7714882-holdings-ltd", 1, "2022-03-04"),
        R("city-of-port-calder", "Paid", "tideway-marine-services", 2, "2022-08-15"),
        R("harbourline-engineering", "Contract administrator for", "city-of-port-calder", 2),
    ],
    requests=[REQ("Payment record", "Invoice and approval memo behind the August 15, 2022 payment of $1,640,000 to "
                  "Tideway Marine Services Ltd.", "Tideway is Northgate's subcontractor; a direct City payment "
                  "needs an explanation", "City Finance Department"),
              REQ("Subcontract", "Northgate's subcontract with Tideway Marine Services for Pier 9 dredging",
                  "Needed to see how the $1,640,000 was authorized", "Northgate Civil Works Ltd.")],
))

# ── 12. City auditor's report ───────────────────────────────────────────────────────────────
DOCS_B.append(dict(
    file="city-auditor-procurement-review-ar-2023-04.pdf", kind="pdf",
    title="Procurement Review: Pier 9 Marine Servicing and the Acquisition of 14 Dockside Road",
    dtype="Audit Report", date="2023-06-12", skill="audit-reports.md",
    source="Office of the City Auditor, report AR-2023-04", obtained="2023-06-12",
    author="Office of the City Auditor", footer="Report AR-2023-04  -  Page {n} of {total}",
    morgue="office-of-the-city-auditor",
    summary=("City Auditor Priya Raman's June 12, 2023 review of the Pier 9 servicing contract and the "
             "purchase of 14 Dockside Road. She finds the contract was not competitively procured and no "
             "written justification was on file; that the contractor's parent was a registered lobbying "
             "client; that the $52,340,000 contract price exceeds Council's $48,600,000 approval by "
             "$3,740,000; that the City paid $4,350,000 for a property appraised at $1,420,000; and that no "
             "conflict was recorded although Councillor Whitcombe chaired the committee and is Marcus "
             "Teague's sister-in-law. Five recommendations follow."),
    scratch=("- Berth permits renewed to 2025, so no urgency existed - matches the Harbour Authority report.\n"
             "- Audit says 'not competitively procured'; the City's news release said it was.\n"
             "- Pike disputes Findings 1 and 4 in his management response.\n"
             "- Teague/Whitcombe family link attributed to the Integrity Commissioner's office."),
    pages=[
        [("letterhead", "Office of the City Auditor", "City of Port Calder  |  Report AR-2023-04"),
         ("title", "Procurement Review: Pier 9 Marine Servicing and the Acquisition of 14 Dockside Road"),
         ("kv", [("Report", "AR-2023-04"), ("Issued", "June 12, 2023"),
                 ("Auditor", "Priya Raman, City Auditor"), ("Presented to", "Audit Committee of Council")]),
         ("h", "Executive Summary"),
         ("p", "We reviewed the procurement of Contract C-2022-041 and the acquisition of 14 Dockside Road. "
               "We found that neither followed the City's Procurement By-law in material respects. Our five "
               "findings and five recommendations follow."),
         ("p", "Our review covered Council and committee records, the procurement file, payments to "
               "September 30, 2022, and the lobbyist registry. We interviewed staff and the Office of the "
               "Integrity Commissioner.")],
        [("h", "Finding 1 - The contract was not competitively procured"),
         ("p", "The award was made under the time-critical works exception in section 14(2) of By-law "
               "2019-114. We found no written justification for the exception in the procurement file."),
         ("p", "The staff report told Council that the Pier 9 berth permits expire on September 30, 2022. The "
               "Harbour Authority renewed those permits on March 3, 2022 for a term ending December 31, 2025, "
               "seven weeks before the award. No time-critical condition existed."),
         ("p", "The City's news release of May 2, 2022 nonetheless described the award as following a "
               "competitive process. It was not competitively procured.")],
        [("h", "Finding 2 - The contractor's parent was a registered lobbying client"),
         ("p", "Northgate Civil Works Ltd. is a wholly owned subsidiary of Meridian Shoreline Developments "
               "Inc., which has been registered to lobby the City since October 4, 2021 (LR-2021-0377). Staff "
               "did not record Northgate's ownership in the procurement file."),
         ("h", "Finding 3 - The contract price exceeds Council's approval"),
         ("p", "Council approved a value not to exceed $48,600,000. The executed contract price is "
               "$52,340,000, a difference of $3,740,000 that was never reported to Council. Progress payments "
               "through September 30, 2022 totalled $23,120,000."),
         ("p", "The additional allowance equals 7.7 percent of the amount Council approved.")],
        [("h", "Finding 4 - The property was acquired at three times its appraised value"),
         ("p", "The City paid $4,350,000 for 14 Dockside Road on February 28, 2022. The only independent "
               "appraisal on file, dated December 14, 2021, valued the property at $1,420,000."),
         ("p", "Council was told the price was $3,900,000 and that an appraisal supported it. The appraisal "
               "was not provided to Council. The seller, 7714882 Holdings Ltd., had bought the property for "
               "$1,150,000 on April 22, 2021."),
         ("p", "The Land Registry records the sale price as $4,350,000. We could not reconcile that figure "
               "with the $3,900,000 in the Council minutes.")],
        [("h", "Finding 5 - No conflict of interest was recorded"),
         ("p", "Councillor Dana Whitcombe chaired the Planning and Procurement Committee that recommended both "
               "transactions. The Office of the Integrity Commissioner told us that Councillor Whitcombe is "
               "the sister-in-law of Marcus Teague, a director of 7714882 Holdings Ltd. until January 15, "
               "2022."),
         ("p", "No declaration of interest appears in the minutes of either Council meeting. The Land Registry "
               "shows Mr. Teague signing the February 28, 2022 transfer as a director of the vendor."),
         ("p", "The Integrity Commissioner opened Inquiry IC-2022-07 on July 20, 2022. That inquiry was "
               "outside the scope of this review.")],
        [("h", "Recommendations"),
         ("table", [["No.", "Recommendation", "Owner", "Due"],
                    ["1", "Refer the Pier 9 award to the Integrity Commissioner", "City Clerk", "2023-07-31"],
                    ["2", "Suspend change orders under C-2022-041", "Procurement", "Immediate"],
                    ["3", "Seek Council ratification of the $3,740,000 allowance", "Procurement", "2023-09-30"],
                    ["4", "Re-tender the remaining Pier 9 work", "Procurement", "2023-12-31"],
                    ["5", "Keep a register of lobbyist-affiliated vendors", "City Clerk", "2023-12-31"]],
          [30, 276, 90, 72]),
         ("h", "Management Response"),
         ("p", "Leonard Pike, Director of Procurement and Real Property, accepted Recommendations 2, 3 and 5 "
               "and disagreed with Findings 1 and 4, stating that the time-critical exception was properly "
               "used and that the price reflected the property's value to the servicing corridor.")],
    ],
    ents=["office-of-the-city-auditor", "priya-raman", "city-of-port-calder", "port-calder-city-council",
          "northgate-civil-works", "meridian-shoreline-developments", "pier-9", "port-calder-harbour-authority",
          "lot-14-dockside-road", "7714882-holdings-ltd", "macaskill-rowe-appraisals", "dana-whitcombe",
          "marcus-teague", "planning-procurement-committee", "office-of-the-integrity-commissioner",
          "integrity-commissioner-inquiry-ic-2022-07", "port-calder-land-registry", "leonard-pike",
          "city-auditor-procurement-review-2023", "anita-sandhu"],
    facts=[
        F(1, "City Auditor Priya Raman issued report AR-2023-04 on June 12, 2023, reviewing the Pier 9 contract "
             "and the purchase of 14 Dockside Road.", ["priya-raman", "office-of-the-city-auditor",
             "city-auditor-procurement-review-2023"],
          q="We reviewed the procurement of Contract C-2022-041", date="2023-06-12"),
        F(2, "The Auditor found no written justification for the time-critical exception in the procurement "
             "file for Contract C-2022-041.", ["office-of-the-city-auditor", "city-of-port-calder",
             "northgate-civil-works"], q="We found no written justification for the exception"),
        F(2, "The Pier 9 berth permits were renewed on March 3, 2022 for a term ending December 31, 2025, "
             "seven weeks before the award, so no time-critical condition existed.",
          ["pier-9", "port-calder-harbour-authority", "office-of-the-city-auditor"],
          q="The Harbour Authority renewed those permits on March 3, 2022", date="2022-03-03"),
        F(2, "The Auditor concluded the Pier 9 contract was not competitively procured, although the City's "
             "May 2, 2022 news release described a competitive process.",
          ["city-of-port-calder", "northgate-civil-works", "office-of-the-city-auditor"],
          q="It was not competitively procured"),
        F(3, "Northgate Civil Works Ltd. is a wholly owned subsidiary of Meridian Shoreline Developments Inc., "
             "registered to lobby the City since October 4, 2021; staff did not record the ownership in the "
             "procurement file.", ["northgate-civil-works", "meridian-shoreline-developments"],
          q="Northgate Civil Works Ltd. is a wholly owned subsidiary of Meridian Shoreline Developments"),
        F(3, "The executed contract price of $52,340,000 exceeds Council's $48,600,000 approval by $3,740,000, "
             "which was never reported to Council.", ["northgate-civil-works", "port-calder-city-council",
             "city-of-port-calder"], q="Council approved a value not to exceed $48,600,000"),
        F(3, "Progress payments to Northgate through September 30, 2022 totalled $23,120,000.",
          ["northgate-civil-works"], q="Progress payments through September 30, 2022 totalled $23,120,000",
          date="2022-09-30"),
        F(3, "The $3,740,000 allowance is 7.7 percent of the amount Council approved.",
          ["northgate-civil-works"], q="The additional allowance equals 7.7 percent"),
        F(4, "The City paid $4,350,000 for 14 Dockside Road on February 28, 2022; the only independent "
             "appraisal, dated December 14, 2021, valued it at $1,420,000.",
          ["lot-14-dockside-road", "city-of-port-calder", "macaskill-rowe-appraisals"],
          q="The City paid $4,350,000 for 14 Dockside Road on February 28, 2022", date="2022-02-28"),
        F(4, "Council was told the price was $3,900,000 and that an appraisal supported it, but the appraisal "
             "was not provided; the seller had bought the property for $1,150,000 on April 22, 2021.",
          ["port-calder-city-council", "7714882-holdings-ltd", "lot-14-dockside-road"],
          q="Council was told the price was $3,900,000 and that an appraisal supported it"),
        F(4, "The Auditor could not reconcile the Land Registry's $4,350,000 sale price with the $3,900,000 in "
             "the Council minutes.", ["port-calder-land-registry", "lot-14-dockside-road"],
          q="The Land Registry records the sale price as $4,350,000"),
        F(5, "Councillor Whitcombe chaired the Planning and Procurement Committee that recommended both "
             "transactions; the Integrity Commissioner's office said she is Marcus Teague's sister-in-law.",
          ["dana-whitcombe", "planning-procurement-committee", "marcus-teague",
           "office-of-the-integrity-commissioner"],
          q="Councillor Dana Whitcombe chaired the Planning and Procurement Committee"),
        F(5, "No declaration of interest appears in the minutes of either Council meeting.",
          ["port-calder-city-council", "dana-whitcombe"],
          q="No declaration of interest appears in the minutes of either Council meeting"),
        F(5, "The Integrity Commissioner opened Inquiry IC-2022-07 on July 20, 2022.",
          ["office-of-the-integrity-commissioner", "integrity-commissioner-inquiry-ic-2022-07"],
          q="The Integrity Commissioner opened Inquiry IC-2022-07 on July 20, 2022", date="2022-07-20"),
        F(6, "Leonard Pike accepted Recommendations 2, 3 and 5 and disagreed with Findings 1 and 4.",
          ["leonard-pike"], q="Leonard Pike, Director of Procurement and Real Property, accepted"),
    ],
    roles=[
        R("priya-raman", "City Auditor of", "city-of-port-calder", 1),
        R("priya-raman", "Reports to", "audit-committee-of-council", 1,
          tname="Audit Committee of Council", ttype="public-body"),
        R("priya-raman", "Led", "city-auditor-procurement-review-2023", 1, "2023"),
        R("office-of-the-city-auditor", "Conducted", "city-auditor-procurement-review-2023", 1, "2023"),
        R("northgate-civil-works", "Subsidiary of", "meridian-shoreline-developments", 3),
        R("dana-whitcombe", "Chair of", "planning-procurement-committee", 5, "2022"),
        R("dana-whitcombe", "Sister-in-law of", "marcus-teague", 5),
        R("marcus-teague", "Director of", "7714882-holdings-ltd", 5, "to 2022-01-15"),
        R("office-of-the-integrity-commissioner", "Opened", "integrity-commissioner-inquiry-ic-2022-07", 5,
          "2022-07-20"),
        R("leonard-pike", "Director of Procurement and Real Property at", "city-of-port-calder", 6),
    ],
    requests=[
        REQ("Procurement file", "Committee scoring sheets and informal bid contacts for the three firms approached "
            "before Contract C-2022-041 was awarded", "The staff report says three firms were approached "
            "informally; the record would show who they were and what they said",
            "City Procurement and Real Property"),
        REQ("Integrity inquiry record", "Notice of Inquiry IC-2022-07 issued by the Integrity Commissioner",
            "Sets out the allegations against Councillor Whitcombe and the dates of the inquiry",
            "Office of the Integrity Commissioner"),
    ],
))

# ── 13. Scanned letter (image) ──────────────────────────────────────────────────────────────
LETTER_LINES = [
    "DANA WHITCOMBE",
    "Councillor, Ward 4",
    "Port Calder City Hall, 1 Civic Square",
    "",
    "August 2, 2022",
    "",
    "Elaine Fortier, Integrity Commissioner",
    "Office of the Integrity Commissioner",
    "",
    "RE: COMPLAINT IC-2022-07",
    "",
    "Dear Commissioner Fortier,",
    "",
    "I write in response to your notice of July 20, 2022 about the complaint filed by",
    "Calder Citizens for Open Government on July 8, 2022.",
    "",
    "I disclosed my family relationship to Marcus Teague, my brother-in-law, to the City",
    "Clerk in writing on April 11, 2022, before the Planning and Procurement Committee",
    "met on April 19, 2022.",
    "",
    "I did not declare an interest at Council on April 26 because the Clerk advised me",
    "that my disclosure was already on file. I have not held, directly or indirectly, any",
    "interest in 7714882 Holdings Ltd. and I have received no benefit from it.",
    "",
    "I understand the minutes of April 26 do not reflect my disclosure. I have asked the",
    "Clerk to correct them. I will co-operate fully with your inquiry.",
    "",
    "Sincerely,",
]
DOCS_B.append(dict(
    file="whitcombe-letter-to-integrity-commissioner.png", kind="png",
    title="Letter from Councillor Dana Whitcombe to the Integrity Commissioner, August 2, 2022",
    dtype="Letter", date="2022-08-02", skill="general-records.md",
    source="Provided by a source, scanned copy", obtained="2023-02-14",
    author=None, footer=None, morgue="dana-whitcombe", ocr=True,
    summary=("A scanned August 2, 2022 letter from Councillor Dana Whitcombe to Integrity Commissioner Elaine "
             "Fortier answering complaint IC-2022-07. Whitcombe says she disclosed her relationship to her "
             "brother-in-law Marcus Teague to the City Clerk in writing on April 11, 2022, did not declare an "
             "interest at the April 26 meeting because the Clerk said the disclosure was on file, denies any "
             "interest in 7714882 Holdings Ltd., and says she has asked the Clerk to correct the minutes."),
    scratch=("- Whitcombe says she disclosed to the Clerk April 11, 2022 - the minutes record no declaration.\n"
             "- Confirms Teague is her brother-in-law.\n- Ask the Clerk for the April 11 disclosure."),
    pages=[LETTER_LINES],
    ents=["dana-whitcombe", "elaine-fortier", "office-of-the-integrity-commissioner",
          "integrity-commissioner-inquiry-ic-2022-07", "calder-citizens-for-open-government", "marcus-teague",
          "anita-sandhu", "planning-and-procurement-committee", "port-calder-city-council",
          "7714882-holdings-ltd", "port-calder-city-hall"],
    facts=[
        F(1, "Councillor Whitcombe wrote to Integrity Commissioner Elaine Fortier on August 2, 2022 in "
             "response to complaint IC-2022-07.", ["dana-whitcombe", "elaine-fortier",
             "integrity-commissioner-inquiry-ic-2022-07"], q="I write in response to your notice of July 20, 2022",
          date="2022-08-02"),
        F(1, "Calder Citizens for Open Government filed complaint IC-2022-07 on July 8, 2022.",
          ["calder-citizens-for-open-government", "integrity-commissioner-inquiry-ic-2022-07"],
          q="Calder Citizens for Open Government on July 8, 2022", date="2022-07-08"),
        F(1, "The Integrity Commissioner sent Whitcombe a notice about the complaint on July 20, 2022.",
          ["office-of-the-integrity-commissioner", "dana-whitcombe"],
          q="I write in response to your notice of July 20, 2022", date="2022-07-20"),
        F(1, "Whitcombe states that Marcus Teague is her brother-in-law and that she disclosed the "
             "relationship to the City Clerk in writing on April 11, 2022, before the Committee met on "
             "April 19.", ["dana-whitcombe", "marcus-teague", "anita-sandhu",
             "planning-and-procurement-committee"],
          q="I disclosed my family relationship to Marcus Teague, my brother-in-law", date="2022-04-11"),
        F(1, "Whitcombe says she did not declare an interest at Council on April 26 because the Clerk advised "
             "her that her disclosure was already on file.", ["dana-whitcombe", "anita-sandhu",
             "port-calder-city-council"],
          q="I did not declare an interest at Council on April 26"),
        F(1, "Whitcombe states she has held no direct or indirect interest in 7714882 Holdings Ltd. and "
             "received no benefit from it.", ["dana-whitcombe", "7714882-holdings-ltd"],
          q="I have not held, directly or indirectly, any interest in 7714882 Holdings Ltd."),
        F(1, "Whitcombe says she has asked the Clerk to correct the April 26 minutes, which do not reflect "
             "her disclosure.", ["dana-whitcombe", "anita-sandhu"],
          q="I understand the minutes of April 26 do not reflect my disclosure"),
    ],
    roles=[
        R("marcus-teague", "Brother-in-law of", "dana-whitcombe", 1),
        R("dana-whitcombe", "Respondent in", "integrity-commissioner-inquiry-ic-2022-07", 1, "2022-07 onward"),
        R("calder-citizens-for-open-government", "Complainant in", "integrity-commissioner-inquiry-ic-2022-07", 1,
          "2022-07-08"),
        R("elaine-fortier", "Integrity Commissioner at", "office-of-the-integrity-commissioner", 1),
        R("elaine-fortier", "Presides over", "integrity-commissioner-inquiry-ic-2022-07", 1),
    ],
    requests=[REQ("Disclosure record", "Written disclosure of family relationship that Councillor Whitcombe says she "
                  "gave the City Clerk on April 11, 2022", "The minutes record no declaration; the letter is the "
                  "only account of a disclosure", "City Clerk")],
))

# ── 14. Reasons for judgment ────────────────────────────────────────────────────────────────
DOCS_B.append(dict(
    file="citizens-v-city-reasons-for-judgment.pdf", kind="pdf",
    title="Calder Citizens for Open Government v. City of Port Calder - Reasons for Judgment",
    dtype="Court Judgment", date="2023-11-30", skill="court-documents.md",
    source="Port Calder Superior Court, published reasons", obtained="2023-12-04",
    author="Port Calder Superior Court", footer="Court File JR-2022-0481  -  Page {n} of {total}",
    morgue="citizens-v-city-judicial-review",
    summary=("Reasons of Justice Ingrid Halvorsen, delivered November 30, 2023, on Calder Citizens for Open "
             "Government's judicial review of Council's April 26, 2022 award of Contract C-2022-041. The Court "
             "finds that bypassing competition on an urgency the record does not support was unreasonable, "
             "sets the award aside as to work not yet performed, orders Council to reconsider within 90 days "
             "and awards the applicant $25,000 in costs. It leaves the conflict question to the Integrity "
             "Commissioner."),
    scratch=("- Court accepts berth permits were renewed to 2025; urgency not supported by the record.\n"
             "- Conflict of interest expressly NOT decided - sits with IC-2022-07.\n"
             "- City counsel: payments to Northgate $41,870,000 as of the Oct 2023 hearing.\n"
             "- Ask whether the City appealed (deadline Dec 2023)."),
    pages=[
        [("letterhead", "Port Calder Superior Court", "Judicial Review  |  Court File JR-2022-0481"),
         ("title", "Calder Citizens for Open Government v. City of Port Calder"),
         ("kv", [("Court file", "JR-2022-0481"), ("Before", "Justice Ingrid Halvorsen"),
                 ("Heard", "October 17 and 18, 2023"), ("Delivered", "November 30, 2023"),
                 ("For the applicant", "Marisol Fernandes"), ("For the respondent", "Counsel for the City")]),
         ("h", "Overview"),
         ("p", "The applicant, a community group chaired by Samir Haddad, seeks judicial review of Council's "
               "April 26, 2022 decision to award Contract C-2022-041 to Northgate Civil Works Ltd. without a "
               "public call for tenders. Northgate was granted leave to intervene on September 12, 2023.")],
        [("h", "Background"),
         ("p", "The applicant filed its notice of application on June 9, 2022. Council had approved the award "
               "on April 26, 2022 at a value not to exceed $48,600,000 under the time-critical works "
               "exception in the City's Procurement By-law."),
         ("p", "The staff report told Council that the Pier 9 berth permits would expire on September 30, "
               "2022. The record shows that the berth permits were renewed on March 3, 2022 for a term ending "
               "December 31, 2025."),
         ("p", "Counsel for the City advised the Court at the hearing that payments to Northgate as of October "
               "17, 2023 totalled $41,870,000.")],
        [("h", "Analysis"),
         ("p", "The applicant has public interest standing. The decision is reviewed for reasonableness."),
         ("p", "A decision to bypass competition on the basis of an urgency that the record does not support "
               "is unreasonable. The record contains no written justification for the exception, and the one "
               "reason staff gave Council was contradicted by the Harbour Authority's renewal of the permits."),
         ("p", "I do not decide whether any councillor was in a conflict of interest. That question is before "
               "the Integrity Commissioner in Inquiry IC-2022-07.")],
        [("h", "Remedy"),
         ("p", "The decision to award Contract C-2022-041 is set aside as to all work not yet performed. "
               "Council shall reconsider the award within 90 days of these reasons. Payments for work already "
               "performed are not affected."),
         ("p", "Costs are fixed at $25,000 payable by the City to the applicant.")],
        [("h", "Order"),
         ("p", "Delivered at Port Calder on November 30, 2023."),
         ("p", "Justice Ingrid Halvorsen."),
         ("small", "Reasons are subject to editorial changes before publication in the official reports.")],
    ],
    ents=["port-calder-superior-court", "ingrid-halvorsen", "citizens-v-city-judicial-review",
          "calder-citizens-for-open-government", "samir-haddad", "marisol-fernandes", "city-of-port-calder",
          "northgate-civil-works", "port-calder-city-council", "pier-9", "port-calder-harbour-authority",
          "integrity-commissioner-inquiry-ic-2022-07"],
    facts=[
        F(1, "Justice Ingrid Halvorsen heard Calder Citizens for Open Government's judicial review of Council's "
             "April 26, 2022 award of Contract C-2022-041 on October 17 and 18, 2023.",
          ["ingrid-halvorsen", "calder-citizens-for-open-government", "citizens-v-city-judicial-review",
           "port-calder-superior-court"], q="The applicant, a community group chaired by Samir Haddad",
          date="2023-10-17"),
        F(1, "Northgate Civil Works Ltd. was granted leave to intervene in the judicial review on September 12, "
             "2023.", ["northgate-civil-works", "citizens-v-city-judicial-review"],
          q="Northgate was granted leave to intervene on September 12, 2023", date="2023-09-12"),
        F(2, "Calder Citizens filed its notice of application on June 9, 2022.",
          ["calder-citizens-for-open-government", "citizens-v-city-judicial-review"],
          q="The applicant filed its notice of application on June 9, 2022", date="2022-06-09"),
        F(2, "The record shows the Pier 9 berth permits were renewed on March 3, 2022 for a term ending "
             "December 31, 2025, although staff told Council they would expire September 30, 2022.",
          ["pier-9", "port-calder-harbour-authority", "city-of-port-calder"],
          q="The record shows that the berth permits were renewed on March 3, 2022", date="2022-03-03"),
        F(2, "City counsel told the Court that payments to Northgate as of October 17, 2023 totalled "
             "$41,870,000.", ["city-of-port-calder", "northgate-civil-works"],
          q="Counsel for the City advised the Court at the hearing that payments to Northgate",
          date="2023-10-17"),
        F(3, "The Court held that bypassing competition on an urgency the record does not support is "
             "unreasonable, and found no written justification for the exception.",
          ["ingrid-halvorsen", "city-of-port-calder", "citizens-v-city-judicial-review"],
          q="A decision to bypass competition on the basis of an urgency that the record does not support"),
        F(3, "The Court declined to decide whether any councillor was in a conflict of interest, leaving it to "
             "the Integrity Commissioner's Inquiry IC-2022-07.",
          ["ingrid-halvorsen", "integrity-commissioner-inquiry-ic-2022-07"],
          q="I do not decide whether any councillor was in a conflict of interest"),
        F(4, "On November 30, 2023 the Court set aside the award of Contract C-2022-041 as to all work not yet "
             "performed and ordered Council to reconsider within 90 days; payments for work done are not "
             "affected.", ["port-calder-superior-court", "port-calder-city-council", "northgate-civil-works",
             "citizens-v-city-judicial-review"],
          q="The decision to award Contract C-2022-041 is set aside as to all work not yet performed",
          date="2023-11-30"),
        F(4, "Costs of $25,000 were fixed against the City in favour of the applicant.",
          ["city-of-port-calder", "calder-citizens-for-open-government"],
          q="Costs are fixed at $25,000 payable by the City"),
    ],
    roles=[
        R("ingrid-halvorsen", "Judge in", "citizens-v-city-judicial-review", 1, "2023"),
        R("samir-haddad", "Chair of", "calder-citizens-for-open-government", 1),
        R("calder-citizens-for-open-government", "Applicant in", "citizens-v-city-judicial-review", 1,
          "2022-06-09 onward"),
        R("city-of-port-calder", "Respondent in", "citizens-v-city-judicial-review", 1),
        R("northgate-civil-works", "Intervenor in", "citizens-v-city-judicial-review", 1, "2023-09-12 onward"),
        R("marisol-fernandes", "Counsel for", "calder-citizens-for-open-government", 1),
        R("port-calder-superior-court", "Heard", "citizens-v-city-judicial-review", 1, "2023-10"),
    ],
    requests=[
        REQ("Court filing", "City of Port Calder's notice of appeal, if filed, from the November 30, 2023 reasons in "
            "JR-2022-0481", "Shows whether the City is contesting the order to reconsider the award",
            "Port Calder Superior Court registry"),
        REQ("Court record", "Certified record of proceedings before Council filed in JR-2022-0481",
            "Includes the materials staff actually put before Council on April 26, 2022",
            "Port Calder Superior Court registry"),
    ],
))

DOCS = None   # assembled in demo_story
