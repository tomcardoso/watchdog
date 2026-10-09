"""Demo documents 1-7: council minutes (two meetings), a corporate registry profile, a land parcel
register, an access-to-information decision, a procurement contract and a lobbyist registration.
Fictional throughout; see `demo_cast`.

Each entry describes the file (`pages` are `demo_pdf` blocks), what a careful extraction of it
would record (`facts`, `roles`, `ents`, `requests`), and the prose around it."""

from watchdog.gui.demo_cast import F, R, REQ

CLERK_HEAD = ("letterhead", "City of Port Calder", "Office of the City Clerk  |  1 Civic Square, Port Calder")
MEMBERS = [
    ["Member", "Ward", "Present"], ["Mayor Robert Delacroix", "At large", "Yes"],
    ["Councillor Dana Whitcombe", "4", "Yes"], ["Councillor Victor Anand", "2", "Yes"],
    ["Councillor Nadia Ferreira", "1", "Yes"], ["Councillor Ben Oyelaran", "6", "Yes"],
    ["Councillor Mei-Lin Chow", "3", "Yes"], ["Councillor Jacques Thibodeau", "5", "Yes"],
    ["Councillor Harpreet Gill", "7", "Yes"], ["Councillor Olu Adeyemi", "8", "Yes"],
    ["Councillor Sofia Marchetti", "9", "Yes"], ["Councillor Walter Kuzmenko", "10", "Yes"],
]
MINUTES_ENTS = ["city-of-port-calder", "port-calder-city-council", "robert-delacroix", "anita-sandhu",
                "port-calder-city-hall"]

DOCS_A = []

# ── 1. Council minutes, February 8, 2022 ────────────────────────────────────────────────────
DOCS_A.append(dict(
    file="council-minutes-2022-02-08.pdf", kind="pdf", title="Minutes of the Regular Meeting of Council, February 8, 2022",
    dtype="Council Minutes", date="2022-02-08", skill="municipal-records.md",
    source="City of Port Calder Clerk's Office, published council minutes", obtained="2024-03-11",
    author="Office of the City Clerk", footer="Council minutes, February 8, 2022  -  Page {n} of {total}",
    morgue="port-calder-city-council",
    summary=("Minutes of Port Calder City Council's February 8, 2022 meeting. Council approved a $6,200,000 "
             "Phase 2 budget for the Harbourfront Lands and voted 8 to 3 to buy 14 Dockside Road from "
             "7714882 Holdings Ltd. for $3,900,000, on the Procurement Director's advice that an "
             "independent appraisal supported the price. The appraisal was not attached to the report "
             "and no member declared a pecuniary interest."),
    scratch=("- The $3,900,000 price differs from the amount later recorded on the land title.\n"
             "- Pike said the vendor would 'entertain other offers' - pressure to decide in one meeting.\n"
             "- Appraisal withheld from members as 'commercially sensitive'."),
    pages=[
        [CLERK_HEAD, ("title", "Minutes of the Regular Meeting of Council"),
         ("kv", [("Date", "Tuesday, February 8, 2022"), ("Time", "6:00 p.m."),
                 ("Location", "Council Chamber, Port Calder City Hall"),
                 ("Presiding", "Mayor Robert Delacroix"), ("Clerk", "Anita Sandhu")]),
         ("h", "1. Call to Order"),
         ("p", "Mayor Robert Delacroix called the meeting to order at 6:02 p.m. All eleven members of "
               "Council were present. The attendance record is set out in Schedule A."),
         ("h", "2. Declarations of Pecuniary Interest"),
         ("p", "The Mayor asked whether any member had a pecuniary interest to declare in any item on the "
               "agenda. No declarations were made."),
         ("h", "3. Adoption of Minutes"),
         ("p", "Moved by Councillor Anand, seconded by Councillor Ferreira, that the minutes of the January "
               "25, 2022 regular meeting be adopted as circulated. Carried.")],
        [("h", "7.1 Harbourfront Renewal Framework - Phase 2 Budget"),
         ("p", "Council received Report CR-2022-007 from the Director of Capital Projects recommending a "
               "Phase 2 capital budget of $6,200,000 for site investigation, shoreline survey and servicing "
               "design on the Harbourfront Lands. Moved by Councillor Ferreira, seconded by Councillor "
               "Anand. Carried 9 to 2."),
         ("h", "7.2 Acquisition of 14 Dockside Road"),
         ("p", "Council received Report CR-2022-011 from Leonard Pike, Director of Procurement and Real "
               "Property. The report recommends that the City acquire the property at 14 Dockside Road "
               "(Lot 14, Plan DP-2217) from 7714882 Holdings Ltd. for $3,900,000, being the amount "
               "supported by an independent appraisal commissioned by the City."),
         ("p", "Mr. Pike advised that the parcel is the last privately held lot on the Pier 9 approach and "
               "is required to extend the servicing corridor. He advised that the vendor had indicated it "
               "would entertain other offers if the City did not move at the February meeting.")],
        [("p", "Councillor Whitcombe, Chair of the Planning and Procurement Committee, reported that the "
               "Committee had reviewed Report CR-2022-011 on January 31, 2022 and recommended approval. "
               "Councillor Oyelaran asked why the appraisal was not appended to the report. Mr. Pike "
               "replied that the appraisal was commercially sensitive and available to members on request."),
         ("p", "Moved by Councillor Whitcombe, seconded by Councillor Anand, that the City acquire 14 "
               "Dockside Road from 7714882 Holdings Ltd. for $3,900,000 plus applicable taxes and closing "
               "costs, and that the Mayor and Clerk be authorized to execute the agreement of purchase and "
               "sale. Carried 8 to 3. Councillors Ferreira and Oyelaran and one other member voted against."),
         ("h", "8. Other Business"),
         ("p", "Councillor Ferreira gave notice that she will ask staff for a report on the City's real "
               "property disclosure practices at the March meeting.")],
        [("h", "9. Adjournment"),
         ("p", "Moved by Councillor Anand that the meeting adjourn at 8:41 p.m. Carried."),
         ("h", "Certification"),
         ("p", "I certify that these are the minutes of the regular meeting of Council held on February 8, "
               "2022. Anita Sandhu, City Clerk."),
         ("h", "Schedule A - Attendance"), ("table", MEMBERS, [220, 100, 100])],
    ],
    ents=MINUTES_ENTS + ["planning-and-procurement-committee", "victor-anand", "nadia-ferreira",
                         "ben-oyelaran", "dana-whitcombe", "leonard-pike", "7714882-holdings-ltd",
                         "lot-14-dockside-road", "pier-9", "harbourfront-lands"],
    facts=[
        F(1, "No member declared a pecuniary interest at the February 8, 2022 meeting.",
          ["port-calder-city-council"], q="No declarations were made", date="2022-02-08"),
        F(2, "Council received a recommendation for a $6,200,000 Phase 2 budget for the Harbourfront Lands "
             "and carried it 9 to 2.", ["port-calder-city-council", "harbourfront-lands"],
          q="Council received Report CR-2022-007", date="2022-02-08"),
        F(2, "Report CR-2022-011 recommended buying 14 Dockside Road from 7714882 Holdings Ltd. for "
             "$3,900,000, described as the amount supported by an independent appraisal commissioned by "
             "the City.", ["lot-14-dockside-road", "7714882-holdings-ltd", "leonard-pike", "city-of-port-calder"],
          q="Council received Report CR-2022-011 from Leonard Pike"),
        F(2, "Leonard Pike told Council the parcel is the last privately held lot on the Pier 9 approach and "
             "that the vendor would entertain other offers if the City did not move at the February meeting.",
          ["leonard-pike", "lot-14-dockside-road", "pier-9", "7714882-holdings-ltd"],
          q="He advised that the vendor had indicated it would entertain other offers"),
        F(3, "Dana Whitcombe, Chair of the Planning and Procurement Committee, reported that the Committee "
             "had reviewed the acquisition report and recommended approval.",
          ["dana-whitcombe", "planning-and-procurement-committee"],
          q="Councillor Whitcombe, Chair of the Planning and Procurement Committee", date="2022-01-31"),
        F(3, "Councillor Oyelaran asked why the appraisal was not appended to the report; Pike replied it was "
             "commercially sensitive and available to members on request.",
          ["ben-oyelaran", "leonard-pike"], q="Councillor Oyelaran asked why the appraisal was not appended"),
        F(3, "Council voted 8 to 3 to acquire 14 Dockside Road from 7714882 Holdings Ltd. for $3,900,000 "
             "plus taxes and closing costs, on a motion by Whitcombe seconded by Anand.",
          ["port-calder-city-council", "lot-14-dockside-road", "7714882-holdings-ltd", "dana-whitcombe",
           "victor-anand"], q="Moved by Councillor Whitcombe, seconded by Councillor Anand, that the City acquire",
          date="2022-02-08"),
        F(3, "Councillor Ferreira gave notice that she would ask staff for a report on the City's real "
             "property disclosure practices.", ["nadia-ferreira"],
          q="Councillor Ferreira gave notice that she will ask staff"),
    ],
    roles=[
        R("dana-whitcombe", "Chair of", "planning-and-procurement-committee", 3, "2022"),
        R("leonard-pike", "Director of Procurement and Real Property at", "city-of-port-calder", 2),
        R("robert-delacroix", "Mayor of", "city-of-port-calder", 1),
        R("anita-sandhu", "City Clerk of", "city-of-port-calder", 1),
        R("planning-and-procurement-committee", "Committee of", "port-calder-city-council", 3),
        R("7714882-holdings-ltd", "Vendor of", "lot-14-dockside-road", 2, "2022-02"),
    ],
    requests=[REQ("Appraisal report", "The independent appraisal of 14 Dockside Road that Report CR-2022-011 "
                  "says supports the $3,900,000 price", "Shows what the City was told the property was worth "
                  "when Council voted", "City Clerk, access to information request")],
))

# ── 2. Council minutes, April 26, 2022 ──────────────────────────────────────────────────────
DOCS_A.append(dict(
    file="council-minutes-2022-04-26.pdf", kind="pdf", title="Minutes of the Regular Meeting of Council, April 26, 2022",
    dtype="Council Minutes", date="2022-04-26", skill="municipal-records.md",
    source="City of Port Calder Clerk's Office, published council minutes", obtained="2024-03-11",
    author="Office of the City Clerk", footer="Council minutes, April 26, 2022  -  Page {n} of {total}",
    morgue="port-calder-city-council",
    summary=("Minutes of the April 26, 2022 Council meeting, at which Council voted 7 to 3 to award Contract "
             "C-2022-041 for Pier 9 marine servicing to Northgate Civil Works Ltd. at a value not to exceed "
             "$48,600,000, without a public call for tenders. A lobbyist for Meridian Shoreline Developments "
             "Inc. addressed Council before the vote. No pecuniary interests were declared."),
    scratch=("- Sole-source under the 'time-critical' exception; stated reason: berth permits expire "
             "September 30, 2022.\n- Okafor-Reyes (Strathmore) spoke for Meridian before the vote.\n"
             "- Whitcombe moved the motion; minutes record no declaration of interest."),
    pages=[
        [CLERK_HEAD, ("title", "Minutes of the Regular Meeting of Council"),
         ("kv", [("Date", "Tuesday, April 26, 2022"), ("Time", "6:00 p.m."),
                 ("Location", "Council Chamber, Port Calder City Hall"),
                 ("Presiding", "Mayor Robert Delacroix"), ("Clerk", "Anita Sandhu")]),
         ("h", "1. Call to Order"),
         ("p", "Mayor Robert Delacroix called the meeting to order at 6:01 p.m. Ten of eleven members were "
               "present. Councillor Ferreira was absent with notice."),
         ("h", "2. Declarations of Pecuniary Interest"),
         ("p", "The Mayor asked whether any member had a pecuniary interest to declare in any item on the "
               "agenda. No declarations of pecuniary interest were made."),
         ("h", "3. Delegations"),
         ("p", "Helen Okafor-Reyes of Strathmore Public Affairs Inc. addressed Council for five minutes on "
               "behalf of Meridian Shoreline Developments Inc. in support of item 9.1, the award of the Pier "
               "9 servicing contract.")],
        [("h", "9.1 Award of Contract C-2022-041 - Pier 9 Marine Servicing"),
         ("p", "Council received Report CR-2022-029 from Leonard Pike, Director of Procurement and Real "
               "Property. The report recommends that Contract C-2022-041 be awarded to Northgate Civil Works "
               "Ltd. at a value not to exceed $48,600,000, under the time-critical works exception in "
               "section 14(2) of the Procurement By-law (By-law 2019-114)."),
         ("p", "Mr. Pike advised that the berth permits for Pier 9 expire on September 30, 2022 and that, "
               "because dredging must begin in June, the City did not issue a public call for tenders. He "
               "advised that three firms were approached informally and that only Northgate confirmed it "
               "could mobilize in time.")],
        [("p", "The Planning and Procurement Committee met on April 19, 2022 under the chairmanship of "
               "Councillor Whitcombe and recommended the award. Councillor Oyelaran asked whether the "
               "recommended contractor had any affiliation with parties that had lobbied the City on the "
               "Harbourfront Lands. Mr. Pike replied that Northgate is a Port Calder firm in good standing "
               "and that staff were aware of no conflict."),
         ("p", "Moved by Councillor Whitcombe, seconded by Councillor Anand, that Contract C-2022-041 be "
               "awarded to Northgate Civil Works Ltd. at a value not to exceed $48,600,000, and that the "
               "Mayor and Clerk be authorized to execute the contract. Carried 7 to 3, with one member "
               "absent. The recorded vote is set out on the next page.")],
        [("h", "Recorded Vote - Item 9.1"),
         ("table", [["Member", "Vote"], ["Mayor Robert Delacroix", "In favour"],
                    ["Councillor Dana Whitcombe", "In favour"], ["Councillor Victor Anand", "In favour"],
                    ["Councillor Mei-Lin Chow", "In favour"], ["Councillor Jacques Thibodeau", "In favour"],
                    ["Councillor Harpreet Gill", "In favour"], ["Councillor Olu Adeyemi", "In favour"],
                    ["Councillor Ben Oyelaran", "Opposed"], ["Councillor Sofia Marchetti", "Opposed"],
                    ["Councillor Walter Kuzmenko", "Opposed"], ["Councillor Nadia Ferreira", "Absent"]],
          [260, 120]),
         ("h", "10. Adjournment"),
         ("p", "The meeting adjourned at 9:12 p.m. I certify that these are the minutes of the regular "
               "meeting of Council held on April 26, 2022. Anita Sandhu, City Clerk.")],
    ],
    ents=MINUTES_ENTS + ["planning-and-procurement-committee", "victor-anand", "ben-oyelaran", "nadia-ferreira",
                         "dana-whitcombe", "leonard-pike", "helen-okafor-reyes", "strathmore-public-affairs",
                         "meridian-shoreline-developments", "northgate-civil-works", "pier-9"],
    facts=[
        F(1, "No declarations of pecuniary interest were made at the April 26, 2022 meeting.",
          ["port-calder-city-council", "dana-whitcombe"],
          q="No declarations of pecuniary interest were made", date="2022-04-26"),
        F(1, "Helen Okafor-Reyes of Strathmore Public Affairs Inc. addressed Council on behalf of Meridian "
             "Shoreline Developments Inc. in support of the Pier 9 servicing award.",
          ["helen-okafor-reyes", "strathmore-public-affairs", "meridian-shoreline-developments", "pier-9"],
          q="Helen Okafor-Reyes of Strathmore Public Affairs Inc. addressed Council", date="2022-04-26"),
        F(2, "Report CR-2022-029 recommended awarding Contract C-2022-041 to Northgate Civil Works Ltd. at a "
             "value not to exceed $48,600,000 under the time-critical works exception, section 14(2) of "
             "By-law 2019-114.", ["northgate-civil-works", "leonard-pike", "city-of-port-calder"],
          q="Council received Report CR-2022-029 from Leonard Pike"),
        F(2, "Pike told Council that the Pier 9 berth permits expire on September 30, 2022 and that the City "
             "therefore issued no public call for tenders; three firms were approached informally and only "
             "Northgate confirmed it could mobilize in time.",
          ["leonard-pike", "pier-9", "northgate-civil-works"],
          q="Mr. Pike advised that the berth permits for Pier 9 expire on September 30, 2022"),
        F(3, "The Planning and Procurement Committee, chaired by Dana Whitcombe, recommended the award at its "
             "April 19, 2022 meeting.", ["planning-and-procurement-committee", "dana-whitcombe"],
          q="The Planning and Procurement Committee met on April 19, 2022", date="2022-04-19"),
        F(3, "Asked by Councillor Oyelaran about contractor affiliations, Pike said Northgate is a Port Calder "
             "firm in good standing and that staff were aware of no conflict.",
          ["leonard-pike", "ben-oyelaran", "northgate-civil-works"],
          q="Mr. Pike replied that Northgate is a Port Calder firm in good standing"),
        F(3, "Council voted 7 to 3, with one member absent, to award Contract C-2022-041 to Northgate Civil "
             "Works Ltd. at a value not to exceed $48,600,000, on a motion by Dana Whitcombe.",
          ["port-calder-city-council", "northgate-civil-works", "dana-whitcombe", "victor-anand"],
          q="Moved by Councillor Whitcombe, seconded by Councillor Anand, that Contract C-2022-041",
          date="2022-04-26"),
        F(4, "Councillors Oyelaran, Marchetti and Kuzmenko opposed the award; Councillor Ferreira was absent.",
          ["ben-oyelaran", "nadia-ferreira"], q="Recorded Vote - Item 9.1"),
    ],
    roles=[
        R("dana-whitcombe", "Chair of", "planning-and-procurement-committee", 3, "2022"),
        R("leonard-pike", "Director of Procurement and Real Property at", "city-of-port-calder", 2),
        R("helen-okafor-reyes", "Lobbyist for", "meridian-shoreline-developments", 1, "2022"),
        R("helen-okafor-reyes", "Partner at", "strathmore-public-affairs", 1),
        R("robert-delacroix", "Mayor of", "city-of-port-calder", 1),
        R("anita-sandhu", "City Clerk of", "city-of-port-calder", 1),
        R("port-calder-city-council", "Awarded contract to", "northgate-civil-works", 3, "2022-04-26"),
    ],
    requests=[
        REQ("Committee records", "Planning and Procurement Committee agenda, report and minutes for April 19, 2022",
            "Shows what the Committee was told before recommending a no-tender award", "City Clerk"),
        REQ("Procurement file", "Written justification for use of the time-critical exception, By-law 2019-114 "
            "s. 14(2), for Contract C-2022-041", "The by-law requires a written justification; its "
            "existence and date would show how the decision was made", "City Procurement and Real Property"),
    ],
))

# ── 3. Corporate profile, 7714882 Holdings Ltd. ─────────────────────────────────────────────
DOCS_A.append(dict(
    file="7714882-holdings-corporate-profile.pdf", kind="pdf",
    title="Corporate Profile Report and Notice of Change - 7714882 Holdings Ltd.",
    dtype="Corporate Registry Filing", date="2022-03-14", skill="corporate-filings.md",
    source="Provincial Business Registry, online search", obtained="2024-03-12",
    author="Provincial Business Registry", footer="Corporation 7714882  -  Page {n} of {total}",
    morgue="7714882-holdings-ltd",
    summary=("Provincial registry profile for 7714882 Holdings Ltd., a numbered company incorporated on March "
             "9, 2021 with Marcus Teague as sole director. A Notice of Change filed January 20, 2022 records "
             "Teague's resignation and the appointment of Tomasz Wieczorek as director effective January 15, "
             "2022, and moves the registered office to 410 Wharf Street, Suite 1200. The registry does not "
             "record beneficial owners."),
    scratch=("- Teague resigned January 15, 2022 - six weeks before the sale to the City closed.\n"
             "- New director Wieczorek is Meridian's CEO; new registered office is Meridian's address.\n"
             "- No beneficial ownership data in this registry."),
    pages=[
        [("letterhead", "Provincial Business Registry", "Corporate Profile Report  |  Business Corporations"),
         ("kv", [("Corporation", "7714882 Holdings Ltd."), ("Number", "7714882"), ("Status", "Active"),
                 ("Registered office", "410 Wharf Street, Suite 1200, Port Calder"),
                 ("Report date", "March 14, 2022")]),
         ("p", "The corporation was incorporated on March 9, 2021 with one director, Marcus Teague, who was "
               "also its president. The current director and president is Tomasz Wieczorek."),
         ("h", "Directors"),
         ("table", [["Name", "Position", "Appointed", "Ceased"],
                    ["Tomasz Wieczorek", "Director, President", "2022-01-15", "-"],
                    ["Marcus Teague", "Director, President", "2021-03-09", "2022-01-15"]],
          [140, 130, 100, 98]),
         ("small", "This report reflects filings received by the Registry as of the report date.")],
        [("title", "Form 4 - Notice of Change"),
         ("kv", [("Corporation", "7714882 Holdings Ltd."), ("Filed", "January 20, 2022"),
                 ("Reference", "PR-7714882-04"), ("Effective", "January 15, 2022")]),
         ("p", "The notice records the resignation of director Marcus Teague, effective January 15, 2022, "
               "and the appointment of Tomasz Wieczorek as director and president on the same date."),
         ("p", "The notice also reports a change of registered office from 88 Gull Lane, Port Calder, to 410 "
               "Wharf Street, Suite 1200, Port Calder, effective January 15, 2022."),
         ("p", "Signed for the corporation by Tomasz Wieczorek, Director, on January 20, 2022.")],
        [("h", "Filing History"),
         ("table", [["Date", "Filing", "Reference"], ["2021-03-09", "Articles of Incorporation", "PR-7714882-01"],
                    ["2021-04-30", "Initial Return", "PR-7714882-02"], ["2022-01-20", "Notice of Change",
                    "PR-7714882-04"], ["2022-03-14", "Corporate Profile Report", "PR-7714882-05"]],
          [100, 238, 130]),
         ("p", "Beneficial ownership is not recorded in this registry. Shareholder information is held by the "
               "corporation and is not filed with the Registrar.")],
    ],
    ents=["7714882-holdings-ltd", "marcus-teague", "tomasz-wieczorek", "410-wharf-street", "88-gull-lane"],
    facts=[
        F(1, "7714882 Holdings Ltd. was incorporated on March 9, 2021 with Marcus Teague as its sole "
             "director and president.", ["7714882-holdings-ltd", "marcus-teague"],
          q="The corporation was incorporated on March 9, 2021", date="2021-03-09"),
        F(1, "Tomasz Wieczorek is the current director and president of 7714882 Holdings Ltd.",
          ["tomasz-wieczorek", "7714882-holdings-ltd"],
          q="The current director and president is Tomasz Wieczorek"),
        F(2, "A Notice of Change filed January 20, 2022 records Marcus Teague's resignation as director "
             "effective January 15, 2022 and Tomasz Wieczorek's appointment the same day.",
          ["marcus-teague", "tomasz-wieczorek", "7714882-holdings-ltd"],
          q="The notice records the resignation of director Marcus Teague", date="2022-01-15"),
        F(2, "7714882 Holdings Ltd. moved its registered office from 88 Gull Lane to 410 Wharf Street, "
             "Suite 1200, effective January 15, 2022.",
          ["7714882-holdings-ltd", "88-gull-lane", "410-wharf-street"],
          q="The notice also reports a change of registered office from 88 Gull Lane", date="2022-01-15"),
        F(2, "The Notice of Change was signed by Tomasz Wieczorek on January 20, 2022.",
          ["tomasz-wieczorek", "7714882-holdings-ltd"],
          q="Signed for the corporation by Tomasz Wieczorek", date="2022-01-20"),
        F(3, "The registry does not record the beneficial owners of 7714882 Holdings Ltd.",
          ["7714882-holdings-ltd"], q="Beneficial ownership is not recorded in this registry"),
    ],
    roles=[
        R("marcus-teague", "Director of", "7714882-holdings-ltd", 1, "2021-03-09 to 2022-01-15"),
        R("marcus-teague", "President of", "7714882-holdings-ltd", 1, "2021-03-09 to 2022-01-15"),
        R("tomasz-wieczorek", "Director of", "7714882-holdings-ltd", 1, "2022-01-15 onward"),
        R("7714882-holdings-ltd", "Registered office at", "410-wharf-street", 2, "2022-01-15 onward"),
        R("7714882-holdings-ltd", "Registered office at", "88-gull-lane", 2, "2021-03-09 to 2022-01-15"),
    ],
    requests=[REQ("Corporate record", "Shareholder register and Articles of Incorporation of 7714882 Holdings Ltd.",
                  "The registry holds no beneficial ownership; the register would show who controlled the "
                  "vendor of 14 Dockside Road", "The corporation, or a court order")],
))

# ── 4. Parcel register, 14 Dockside Road ────────────────────────────────────────────────────
DOCS_A.append(dict(
    file="parcel-register-14-dockside-road.pdf", kind="pdf",
    title="Parcel Register - PIN 40318-0147, 14 Dockside Road", dtype="Land Title Parcel Register",
    date="2022-03-21", skill="real-estate.md",
    source="Port Calder Land Registry Office, online parcel register", obtained="2024-03-12",
    author="Port Calder Land Registry Office", footer="PIN 40318-0147  -  Page {n} of {total}",
    morgue="lot-14-dockside-road",
    summary=("Land titles parcel register for 14 Dockside Road (PIN 40318-0147). The Estate of Albert Kessler "
             "sold it to 7714882 Holdings Ltd. on April 22, 2021 for $1,150,000, with an $800,000 charge to "
             "First Maritime Credit Union. On February 28, 2022 the numbered company sold it to the City of "
             "Port Calder for $4,350,000; the transfer was signed for the vendor by Marcus Teague as "
             "Director. The charge was discharged on March 2, 2022."),
    scratch=("- Sale to the City: $4,350,000 on the register vs $3,900,000 in Council's minutes.\n"
             "- Teague signed as Director on Feb 28, 2022 - after his registered resignation (Jan 15).\n"
             "- Held 10 months; resold at roughly 3.8x the purchase price."),
    pages=[
        [("letterhead", "Port Calder Land Registry Office", "Land Titles Division  |  Parcel Register (Abbreviated)"),
         ("kv", [("PIN", "40318-0147 (LT)"), ("Property", "14 Dockside Road, Port Calder"),
                 ("Description", "Lot 14, Plan DP-2217, City of Port Calder"), ("Estate", "Fee simple"),
                 ("Registered owner", "City of Port Calder"), ("Printed", "March 21, 2022")]),
         ("h", "Registrar's Summary"),
         ("p", "Instrument PC-418802, registered April 22, 2021, transferred the property from the Estate of "
               "Albert Kessler to 7714882 Holdings Ltd. for stated consideration of $1,150,000."),
         ("p", "Instrument PC-418803, registered April 22, 2021, is a charge of $800,000 in favour of First "
               "Maritime Credit Union against the property."),
         ("p", "Instrument PC-447119, registered February 28, 2022, transferred the property from 7714882 "
               "Holdings Ltd. to the City of Port Calder for stated consideration of $4,350,000."),
         ("table", [["Registered", "Instrument", "Type", "Parties", "Amount"],
                    ["2021-04-22", "PC-418802", "Transfer", "Kessler Estate to 7714882 Holdings", "$1,150,000"],
                    ["2021-04-22", "PC-418803", "Charge", "7714882 Holdings to First Maritime", "$800,000"],
                    ["2022-02-28", "PC-447119", "Transfer", "7714882 Holdings to City of Port Calder", "$4,350,000"],
                    ["2022-03-02", "PC-447377", "Discharge", "First Maritime, charge PC-418803", "-"]],
          [60, 62, 58, 215, 73])],
        [("h", "Instrument Notes"),
         ("p", "Transfer PC-447119 was executed on behalf of the transferor by Marcus Teague, Director, who "
               "declared that he had authority to bind the corporation. The transferee was represented by "
               "Anita Sandhu, City Clerk."),
         ("p", "Instrument PC-447377, registered March 2, 2022, discharges charge PC-418803 in full."),
         ("p", "The Estate of Albert Kessler held the property from 1987 until April 22, 2021."),
         ("small", "This abbreviated register is provided for information and is not a certificate of title.")],
    ],
    ents=["port-calder-land-registry-office", "lot-14-dockside-road", "7714882-holdings-ltd", "kessler-estate",
          "first-maritime-credit-union", "charge-pc-418803", "city-of-port-calder", "marcus-teague",
          "anita-sandhu"],
    facts=[
        F(1, "On April 22, 2021 the Estate of Albert Kessler transferred 14 Dockside Road to 7714882 Holdings "
             "Ltd. for $1,150,000 (Instrument PC-418802).",
          ["kessler-estate", "7714882-holdings-ltd", "lot-14-dockside-road"],
          q="Instrument PC-418802, registered April 22, 2021", date="2021-04-22"),
        F(1, "7714882 Holdings Ltd. granted First Maritime Credit Union an $800,000 charge against 14 Dockside "
             "Road on April 22, 2021 (Instrument PC-418803).",
          ["7714882-holdings-ltd", "first-maritime-credit-union", "charge-pc-418803", "lot-14-dockside-road"],
          q="Instrument PC-418803, registered April 22, 2021, is a charge of $800,000", date="2021-04-22"),
        F(1, "On February 28, 2022 7714882 Holdings Ltd. transferred 14 Dockside Road to the City of Port "
             "Calder for stated consideration of $4,350,000 (Instrument PC-447119).",
          ["7714882-holdings-ltd", "city-of-port-calder", "lot-14-dockside-road"],
          q="Instrument PC-447119, registered February 28, 2022", date="2022-02-28"),
        F(2, "The February 28, 2022 transfer was executed for 7714882 Holdings Ltd. by Marcus Teague, "
             "Director, who declared authority to bind the corporation; Anita Sandhu acted for the City.",
          ["marcus-teague", "7714882-holdings-ltd", "anita-sandhu", "lot-14-dockside-road"],
          q="Transfer PC-447119 was executed on behalf of the transferor by Marcus Teague, Director",
          date="2022-02-28"),
        F(2, "The First Maritime charge on 14 Dockside Road was discharged in full on March 2, 2022 "
             "(Instrument PC-447377).", ["first-maritime-credit-union", "charge-pc-418803", "lot-14-dockside-road"],
          q="Instrument PC-447377, registered March 2, 2022, discharges charge", date="2022-03-02"),
        F(2, "The Estate of Albert Kessler held 14 Dockside Road from 1987 until April 22, 2021.",
          ["kessler-estate", "lot-14-dockside-road"], q="The Estate of Albert Kessler held the property from 1987"),
        F(1, "7714882 Holdings Ltd. held the property for about ten months and sold it to the City for roughly "
             "3.8 times what it paid.", ["7714882-holdings-ltd", "lot-14-dockside-road", "city-of-port-calder"],
          inferred=True, date="2022-02-28"),
    ],
    roles=[
        R("kessler-estate", "Seller of", "lot-14-dockside-road", 1, "to 2021-04-22"),
        R("7714882-holdings-ltd", "Owner of", "lot-14-dockside-road", 1, "2021-04-22 to 2022-02-28"),
        R("city-of-port-calder", "Owner of", "lot-14-dockside-road", 1, "2022-02-28 onward"),
        R("first-maritime-credit-union", "Lender to", "7714882-holdings-ltd", 1, "2021-04-22 to 2022-03-02"),
        R("first-maritime-credit-union", "Holder of", "charge-pc-418803", 1, "2021-04-22 to 2022-03-02"),
        R("marcus-teague", "Signed transfer for", "7714882-holdings-ltd", 2, "2022-02-28"),
    ],
    requests=[REQ("Land transfer record", "Statement of land transfer tax affidavit for Instrument PC-447119",
                  "Affidavits of value can show whether the stated consideration matches the money that moved",
                  "Port Calder Land Registry Office")],
))

# ── 5. Access-to-information decision ───────────────────────────────────────────────────────
DOCS_A.append(dict(
    file="foi-response-lot-14-appraisal.pdf", kind="pdf",
    title="Access to Information Decision AR-2022-217 - Appraisal of 14 Dockside Road",
    dtype="Access to Information Response", date="2023-01-19", skill="foi-responses.md",
    source="City of Port Calder Clerk's Office, response to a records request", obtained="2023-01-19",
    author="Office of the City Clerk", footer="Decision AR-2022-217  -  Page {n} of {total}",
    morgue="lot-14-dockside-road",
    summary=("The City Clerk's decision on an access request for the appraisal of 14 Dockside Road. The City "
             "released, in part, a December 14, 2021 appraisal by Macaskill & Rowe Appraisals Ltd. valuing the "
             "property at $1,420,000 as of December 10, 2021, and said it could not locate a second staff "
             "valuation referred to in a January 24, 2022 e-mail."),
    scratch=("- Appraised value $1,420,000 vs Council price $3,900,000 vs title price $4,350,000.\n"
             "- A 'second valuation' is referred to in staff e-mail but cannot be found - ask for the e-mail.\n"
             "- Appraisal marked for internal budgeting only; assumed industrial zoning."),
    pages=[
        [CLERK_HEAD, ("title", "Access to Information Decision"),
         ("kv", [("Request", "AR-2022-217"), ("Decision date", "January 19, 2023"),
                 ("Requester", "Name withheld"), ("Decision", "Disclosed in part")]),
         ("p", "You asked for the independent appraisal the City relied on in acquiring 14 Dockside Road, "
               "and for any staff valuation of the property prepared between September 2021 and February 2022."),
         ("p", "The City located one responsive appraisal report, prepared by Macaskill & Rowe Appraisals Ltd. "
               "and dated December 14, 2021. It is released in part. Personal information and the "
               "appraiser's licence number have been severed under section 14 of the Act."),
         ("p", "A second valuation, referred to in a staff e-mail of January 24, 2022, could not be located "
               "after a search of the files of the Procurement and Real Property division.")],
        [("h", "Released Portions of the Appraisal"),
         ("kv", [("Property", "14 Dockside Road, Port Calder"), ("Appraiser", "Owen Macaskill, Macaskill & Rowe"),
                 ("Client", "City of Port Calder, Procurement and Real Property"),
                 ("Valuation date", "December 10, 2021"), ("Report date", "December 14, 2021")]),
         ("p", "The released opinion of value reads: the market value of the property was $1,420,000 as of "
               "December 10, 2021."),
         ("p", "The report states that the opinion was prepared on the assumption of the existing industrial "
               "zoning and does not consider any servicing corridor or redevelopment potential."),
         ("p", "The cover page of the report is marked prepared for internal budgeting purposes only.")],
        [("h", "Context Provided by the Clerk"),
         ("p", "For context, Council's minutes of February 8, 2022 record a purchase price of $3,900,000 for "
               "the property. That figure is not derived from the appraisal released to you."),
         ("p", "Fees: no fee is charged for this decision. You may ask the Information Commissioner to review "
               "this decision within 30 days of receiving it."),
         ("p", "Anita Sandhu, City Clerk and Access Coordinator, January 19, 2023.")],
        # The released cover page, as the Clerk's office scanned it: no text layer of its own.
        [("scan", ["MACASKILL & ROWE APPRAISALS LTD.", "",
                   "Appraisal Report", "14 Dockside Road, Port Calder", "",
                   "Prepared for: City of Port Calder, Procurement and Real Property",
                   "Effective date of value: December 10, 2021",
                   "Report date: December 14, 2021", "",
                   "PREPARED FOR INTERNAL BUDGETING PURPOSES ONLY", "",
                   "Appraiser's licence no.: [severed, s. 14]"])],
    ],
    ents=["city-of-port-calder", "anita-sandhu", "macaskill-rowe-appraisals", "owen-macaskill",
          "lot-14-dockside-road", "port-calder-city-council"],
    facts=[
        F(1, "The City located one responsive appraisal of 14 Dockside Road, prepared by Macaskill & Rowe "
             "Appraisals Ltd. and dated December 14, 2021, and released it in part.",
          ["city-of-port-calder", "macaskill-rowe-appraisals", "lot-14-dockside-road"],
          q="The City located one responsive appraisal report", date="2021-12-14"),
        F(1, "A staff e-mail of January 24, 2022 refers to a second valuation of the property; the City could "
             "not locate it.", ["city-of-port-calder", "lot-14-dockside-road"],
          q="A second valuation, referred to in a staff e-mail of January 24, 2022", date="2022-01-24"),
        F(2, "The released appraisal puts the market value of 14 Dockside Road at $1,420,000 as of December "
             "10, 2021.", ["macaskill-rowe-appraisals", "owen-macaskill", "lot-14-dockside-road"],
          q="The released opinion of value reads", date="2021-12-10"),
        F(2, "The appraisal assumed existing industrial zoning and did not consider a servicing corridor or "
             "redevelopment potential.", ["macaskill-rowe-appraisals", "lot-14-dockside-road"],
          q="The report states that the opinion was prepared on the assumption"),
        F(2, "The appraisal's cover page is marked prepared for internal budgeting purposes only.",
          ["macaskill-rowe-appraisals"], q="The cover page of the report is marked prepared for internal budgeting"),
        F(3, "The Clerk notes that Council's February 8, 2022 minutes record a purchase price of $3,900,000, "
             "which is not derived from the released appraisal.",
          ["anita-sandhu", "port-calder-city-council", "lot-14-dockside-road"],
          q="For context, Council's minutes of February 8, 2022 record a purchase price"),
        F(3, "Council's stated price of $3,900,000 was about 2.7 times the appraised $1,420,000.",
          ["lot-14-dockside-road", "macaskill-rowe-appraisals"], inferred=True),
    ],
    roles=[
        R("anita-sandhu", "City Clerk of", "city-of-port-calder", 3),
        R("owen-macaskill", "Appraiser at", "macaskill-rowe-appraisals", 2),
        R("macaskill-rowe-appraisals", "Appraised", "lot-14-dockside-road", 2, "2021-12-10"),
    ],
    requests=[
        REQ("E-mail", "The staff e-mail of January 24, 2022 that refers to a second valuation of 14 Dockside Road",
            "Would identify who commissioned a second valuation and what it concluded",
            "City Procurement and Real Property, by access request"),
        REQ("Appraisal report", "Complete, unsevered appraisal of 14 Dockside Road by Macaskill & Rowe Appraisals Ltd., "
            "dated December 14, 2021", "The released version is severed and marked for internal budgeting only",
            "Macaskill & Rowe Appraisals Ltd."),
    ],
))

# ── 6. Procurement contract ─────────────────────────────────────────────────────────────────
DOCS_A.append(dict(
    file="contract-c-2022-041-pier-9-servicing.pdf", kind="pdf",
    title="Contract C-2022-041 - Pier 9 Marine Servicing Works", dtype="Procurement Contract",
    date="2022-05-17", skill="government-contracts.md",
    source="City of Port Calder Procurement and Real Property, contract disclosure", obtained="2023-02-06",
    author="City of Port Calder Procurement and Real Property",
    footer="Contract C-2022-041  -  Page {n} of {total}", morgue="northgate-civil-works",
    summary=("Contract C-2022-041 between the City of Port Calder and Northgate Civil Works Ltd., effective May "
             "17, 2022, for dredging, quay-wall reconstruction and servicing at Pier 9. The total price is "
             "$52,340,000: Council's $48,600,000 ceiling plus a $3,740,000 mobilization and contingency "
             "allowance. Northgate is a wholly owned subsidiary of Meridian Shoreline Developments Inc. "
             "Extra payments need only a change order signed by the Procurement Director."),
    scratch=("- Contract price $52,340,000 exceeds the $48,600,000 Council approved by $3,740,000.\n"
             "- Northgate declares Meridian as its parent; Meridian's CEO Wieczorek witnessed the signing.\n"
             "- Tideway subcontract (dredging) is 'separate' and not attached."),
    pages=[
        [("letterhead", "City of Port Calder", "Procurement and Real Property Division  |  1 Civic Square"),
         ("title", "Contract C-2022-041 - Pier 9 Marine Servicing Works"),
         ("kv", [("Contract number", "C-2022-041"), ("Owner", "City of Port Calder (the City)"),
                 ("Contractor", "Northgate Civil Works Ltd., 410 Wharf Street, Suite 1100, Port Calder"),
                 ("Effective date", "May 17, 2022"),
                 ("Authority", "Council resolution of April 26, 2022 (Report CR-2022-029)"),
                 ("Procurement", "Time-critical works exception, By-law 2019-114, s. 14(2)")]),
         ("p", "This contract sets out the terms on which the Contractor will carry out dredging, quay-wall "
               "reconstruction and underground servicing at Pier 9, Calder Harbour."),
         ("p", "The Contractor is a wholly owned subsidiary of Meridian Shoreline Developments Inc., which "
               "guarantees the Contractor's obligations under this contract.")],
        [("h", "2. Contract Price"),
         ("p", "The Total Contract Price is $52,340,000, being the base price of $48,600,000 plus a "
               "mobilization and contingency allowance of $3,740,000."),
         ("table", [["Item", "Amount"], ["Base price", "$48,600,000"],
                    ["Mobilization and contingency allowance", "$3,740,000"],
                    ["Total Contract Price", "$52,340,000"]], [300, 168]),
         ("p", "The Contractor shall not be paid more than the Total Contract Price without a written change "
               "order signed by the Director of Procurement and Real Property."),
         ("p", "The Contractor shall deliver Performance Bond PB-88213 in the amount of $26,170,000, being 50 "
               "percent of the Total Contract Price, before the first progress payment. The bond is issued by "
               "Harbourgate Surety Company.")],
        [("h", "3. Schedule"),
         ("p", "Substantial completion is required by October 31, 2024. Mobilization begins on June 6, 2022."),
         ("table", [["Milestone", "Date"], ["Mobilization", "2022-06-06"], ["Dredging complete", "2023-03-31"],
                    ["Quay wall complete", "2023-12-15"], ["Servicing complete", "2024-08-30"],
                    ["Substantial completion", "2024-10-31"]], [300, 168]),
         ("p", "Progress payments are made monthly on the certificate of the Contract Administrator.")],
        [("h", "4. Subcontractors and Consultants"),
         ("p", "The Contractor has engaged Tideway Marine Services Ltd. to carry out dredging using the "
               "Dredge Calder Princess under a separate subcontract that does not form part of this contract."),
         ("p", "Harbourline Engineering Inc. is the Contract Administrator for the City and certifies the "
               "Contractor's progress claims."),
         ("p", "The Contractor shall not assign this contract or any subcontract to the City's detriment.")],
        [("h", "5. Execution"),
         ("p", "Signed for the City by Robert Delacroix, Mayor, and Anita Sandhu, City Clerk, on May 17, 2022."),
         ("p", "Signed for the Contractor by Colleen Brandt, President, on May 17, 2022."),
         ("p", "Witness for the Contractor: Tomasz Wieczorek, Chief Executive Officer, Meridian Shoreline "
               "Developments Inc.")],
    ],
    ents=["city-of-port-calder", "northgate-civil-works", "meridian-shoreline-developments", "410-wharf-street",
          "pier-9", "calder-harbour", "performance-bond-pb-88213", "tideway-marine-services",
          "dredge-calder-princess", "harbourline-engineering", "robert-delacroix", "anita-sandhu",
          "colleen-brandt", "tomasz-wieczorek", "leonard-pike"],
    facts=[
        F(1, "Contract C-2022-041 between the City of Port Calder and Northgate Civil Works Ltd. took effect "
             "on May 17, 2022 for dredging, quay-wall reconstruction and servicing at Pier 9.",
          ["city-of-port-calder", "northgate-civil-works", "pier-9"],
          q="This contract sets out the terms on which the Contractor", date="2022-05-17"),
        F(1, "Northgate Civil Works Ltd. is a wholly owned subsidiary of Meridian Shoreline Developments Inc., "
             "which guarantees Northgate's obligations under the contract.",
          ["northgate-civil-works", "meridian-shoreline-developments"],
          q="The Contractor is a wholly owned subsidiary of Meridian Shoreline Developments Inc."),
        F(1, "Northgate's address in the contract is 410 Wharf Street, Suite 1100.",
          ["northgate-civil-works", "410-wharf-street"], q="Contractor Northgate Civil Works Ltd., 410 Wharf Street"),
        F(2, "The Total Contract Price is $52,340,000: a $48,600,000 base price plus a $3,740,000 mobilization "
             "and contingency allowance.", ["northgate-civil-works", "city-of-port-calder"],
          q="The Total Contract Price is $52,340,000"),
        F(2, "Payments above the Total Contract Price require only a written change order signed by the "
             "Director of Procurement and Real Property.", ["leonard-pike", "northgate-civil-works"],
          q="The Contractor shall not be paid more than the Total Contract Price"),
        F(2, "Northgate must deliver Performance Bond PB-88213 for $26,170,000, 50 percent of the price, "
             "before the first progress payment.", ["northgate-civil-works", "performance-bond-pb-88213"],
          q="The Contractor shall deliver Performance Bond PB-88213"),
        F(3, "Mobilization under the contract begins on June 6, 2022.", ["northgate-civil-works", "pier-9"],
          q="Mobilization begins on June 6, 2022", date="2022-06-06"),
        F(3, "Substantial completion of the works is required by October 31, 2024.",
          ["northgate-civil-works", "pier-9"], q="Substantial completion is required by October 31, 2024",
          date="2024-10-31"),
        F(4, "Northgate engaged Tideway Marine Services Ltd. to dredge using the Dredge Calder Princess under a "
             "separate subcontract that is not part of the City contract.",
          ["tideway-marine-services", "dredge-calder-princess", "northgate-civil-works"],
          q="The Contractor has engaged Tideway Marine Services Ltd."),
        F(4, "Harbourline Engineering Inc. is the City's Contract Administrator and certifies Northgate's "
             "progress claims.", ["harbourline-engineering", "northgate-civil-works", "city-of-port-calder"],
          q="Harbourline Engineering Inc. is the Contract Administrator"),
        F(5, "Mayor Robert Delacroix and Clerk Anita Sandhu signed for the City, Colleen Brandt for Northgate, "
             "and Meridian CEO Tomasz Wieczorek witnessed for the Contractor.",
          ["robert-delacroix", "anita-sandhu", "colleen-brandt", "tomasz-wieczorek"],
          q="Witness for the Contractor", date="2022-05-17"),
    ],
    roles=[
        R("northgate-civil-works", "Subsidiary of", "meridian-shoreline-developments", 1),
        R("meridian-shoreline-developments", "Guarantor of", "northgate-civil-works", 1, "2022-05-17"),
        R("city-of-port-calder", "Awarded contract to", "northgate-civil-works", 1, "2022-05-17"),
        R("northgate-civil-works", "Located at", "410-wharf-street", 1),
        R("colleen-brandt", "President of", "northgate-civil-works", 5),
        R("tomasz-wieczorek", "Chief Executive Officer of", "meridian-shoreline-developments", 5),
        R("tideway-marine-services", "Subcontractor to", "northgate-civil-works", 4),
        R("tideway-marine-services", "Operator of", "dredge-calder-princess", 4),
        R("harbourline-engineering", "Contract administrator for", "city-of-port-calder", 4),
        R("northgate-civil-works", "Principal on", "performance-bond-pb-88213", 2),
        R("performance-bond-pb-88213", "Issued by", "harbourgate-surety-company", 2,
          tname="Harbourgate Surety Company", ttype="organization"),
        R("robert-delacroix", "Mayor of", "city-of-port-calder", 5),
        R("anita-sandhu", "City Clerk of", "city-of-port-calder", 5),
    ],
    requests=[REQ("Subcontract", "Subcontract between Northgate Civil Works Ltd. and Tideway Marine Services Ltd. for "
                  "Pier 9 dredging", "Shows the dredging price and who is paid for it; the City contract "
                  "excludes it", "Northgate Civil Works Ltd., or the City under the contract's records clause")],
))

# ── 7. Lobbyist registration ────────────────────────────────────────────────────────────────
DOCS_A.append(dict(
    file="lobbyist-registration-lr-2021-0377.pdf", kind="pdf",
    title="Lobbyist Registration LR-2021-0377 - Strathmore Public Affairs Inc. for Meridian Shoreline Developments Inc.",
    dtype="Lobbyist Registration", date="2022-05-19", skill="lobbying-records.md",
    source="Port Calder Lobbyist Registry, public search", obtained="2024-03-12",
    author="Office of the Lobbyist Registrar", footer="Registration LR-2021-0377  -  Page {n} of {total}",
    morgue="meridian-shoreline-developments",
    summary=("Public lobbyist registration LR-2021-0377, in which Helen Okafor-Reyes of Strathmore Public "
             "Affairs Inc. lobbies the City for Meridian Shoreline Developments Inc. on the Harbourfront Lands, "
             "including the City's purchase of 14 Dockside Road and the Pier 9 servicing contract. Northgate "
             "Civil Works Ltd. was added as an affiliate on May 19, 2022. Blackwater Capital Partners LP holds "
             "35 percent of the client. Contacts with Councillor Whitcombe and Leonard Pike are listed."),
    scratch=("- Registered October 4, 2021: lobbying covered the Lot 14 sale to the City before it happened.\n"
             "- Contacts with Whitcombe (Nov 17, Feb 2, Apr 12) bracket both Council votes.\n"
             "- Northgate only added as an affiliate AFTER the award (May 19, 2022)."),
    pages=[
        [("letterhead", "Office of the Lobbyist Registrar", "City of Port Calder  |  Public Registry of Lobbyists"),
         ("kv", [("Registration", "LR-2021-0377"), ("Type", "Consultant lobbyist"),
                 ("Lobbyist", "Helen Okafor-Reyes, Senior Partner, Strathmore Public Affairs Inc."),
                 ("Client", "Meridian Shoreline Developments Inc., 410 Wharf Street, Suite 1200"),
                 ("Responsible officer", "Tomasz Wieczorek, Chief Executive Officer"),
                 ("Registered", "October 4, 2021"), ("Last updated", "May 19, 2022"),
                 ("Client website", "meridianshoreline.example")]),
         ("p", "The registered subject matters are the redevelopment of the Harbourfront Lands, including the "
               "City's acquisition of 14 Dockside Road, and the procurement of marine servicing works at "
               "Pier 9."),
         ("p", "Northgate Civil Works Ltd. was added as an affiliate of the client with an interest in the "
               "subject matters on May 19, 2022."),
         ("p", "Blackwater Capital Partners LP holds a 35 percent interest in the client, as disclosed by "
               "the client.")],
        [("h", "Communications Reported"),
         ("bullets", ["November 17, 2021 - meeting with Councillor Dana Whitcombe about the acquisition of 14 "
                      "Dockside Road.",
                      "February 2, 2022 - telephone call with Councillor Dana Whitcombe about the Council report "
                      "on 14 Dockside Road.",
                      "April 12, 2022 - meeting with Councillor Dana Whitcombe about procurement of the Pier 9 "
                      "works.",
                      "April 14, 2022 - meeting with Leonard Pike, Director of Procurement and Real Property, "
                      "about the Pier 9 time-critical exception."]),
         ("p", "A consultant lobbyist is not required to disclose fees. Communications are reported within "
               "15 days of the contact."),
         ("small", "Registration LR-2021-0377 remains active. Updates are published on the public registry.")],
    ],
    ents=["office-of-the-lobbyist-registrar", "helen-okafor-reyes", "strathmore-public-affairs",
          "meridian-shoreline-developments", "tomasz-wieczorek", "410-wharf-street", "meridianshoreline-example",
          "northgate-civil-works", "blackwater-capital-partners", "dana-whitcombe", "leonard-pike",
          "lot-14-dockside-road", "pier-9", "harbourfront-lands"],
    facts=[
        F(1, "Helen Okafor-Reyes of Strathmore Public Affairs Inc. registered on October 4, 2021 to lobby the "
             "City for Meridian Shoreline Developments Inc.",
          ["helen-okafor-reyes", "strathmore-public-affairs", "meridian-shoreline-developments"],
          q="The registered subject matters are the redevelopment of the Harbourfront Lands", date="2021-10-04"),
        F(1, "The registered subject matters include the City's acquisition of 14 Dockside Road and the "
             "procurement of Pier 9 marine servicing works.",
          ["meridian-shoreline-developments", "lot-14-dockside-road", "pier-9", "harbourfront-lands"],
          q="The registered subject matters are the redevelopment of the Harbourfront Lands"),
        F(1, "Northgate Civil Works Ltd. was added as an affiliate of Meridian with an interest in the "
             "lobbying subject matters on May 19, 2022.",
          ["northgate-civil-works", "meridian-shoreline-developments"],
          q="Northgate Civil Works Ltd. was added as an affiliate of the client", date="2022-05-19"),
        F(1, "Blackwater Capital Partners LP holds a 35 percent interest in Meridian Shoreline Developments "
             "Inc., as disclosed by the client.", ["blackwater-capital-partners", "meridian-shoreline-developments"],
          q="Blackwater Capital Partners LP holds a 35 percent interest"),
        F(2, "Okafor-Reyes reported a meeting with Councillor Dana Whitcombe about the acquisition of 14 "
             "Dockside Road.", ["helen-okafor-reyes", "dana-whitcombe", "lot-14-dockside-road"],
          q="November 17, 2021 - meeting with Councillor Dana Whitcombe", date="2021-11-17"),
        F(2, "Okafor-Reyes reported a telephone call with Whitcombe about the Council report on 14 Dockside "
             "Road.", ["helen-okafor-reyes", "dana-whitcombe", "lot-14-dockside-road"],
          q="February 2, 2022 - telephone call with Councillor Dana Whitcombe", date="2022-02-02"),
        F(2, "Okafor-Reyes reported a meeting with Whitcombe about procurement of the Pier 9 works.",
          ["helen-okafor-reyes", "dana-whitcombe", "pier-9"],
          q="April 12, 2022 - meeting with Councillor Dana Whitcombe", date="2022-04-12"),
        F(2, "Okafor-Reyes reported a meeting with Leonard Pike about the Pier 9 time-critical exception.",
          ["helen-okafor-reyes", "leonard-pike", "pier-9"],
          q="April 14, 2022 - meeting with Leonard Pike", date="2022-04-14"),
        F(2, "Two of the three reported contacts with Whitcombe fell within a week of a Council or committee "
             "decision on the matters lobbied.", ["dana-whitcombe", "helen-okafor-reyes"], inferred=True,
          date="2022-04-12"),
    ],
    roles=[
        R("helen-okafor-reyes", "Lobbyist for", "meridian-shoreline-developments", 1, "2021-10-04 onward"),
        R("helen-okafor-reyes", "Senior Partner at", "strathmore-public-affairs", 1),
        R("strathmore-public-affairs", "Lobbyist for", "meridian-shoreline-developments", 1, "2021-10-04 onward"),
        R("tomasz-wieczorek", "Chief Executive Officer of", "meridian-shoreline-developments", 1),
        R("blackwater-capital-partners", "Investor in", "meridian-shoreline-developments", 1),
        R("northgate-civil-works", "Affiliate of", "meridian-shoreline-developments", 1, "2022-05-19 onward"),
        R("meridian-shoreline-developments", "Located at", "410-wharf-street", 1),
        R("meridianshoreline-example", "Website of", "meridian-shoreline-developments", 1),
        R("helen-okafor-reyes", "Lobbied", "dana-whitcombe", 2, "2021-11-17 to 2022-04-12"),
        R("helen-okafor-reyes", "Lobbied", "leonard-pike", 2, "2022-04-14"),
    ],
    requests=[REQ("Lobbying communications", "Monthly communication reports filed by Strathmore Public Affairs Inc. under "
                  "LR-2021-0377, including any contacts not listed on the public summary",
                  "Would show the full pattern of contact before the two votes", "Office of the Lobbyist Registrar")],
))
