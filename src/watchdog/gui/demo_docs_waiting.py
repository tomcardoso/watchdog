"""The sample's loose ends that have prepared answers too (D307): the two files left waiting in
`incoming/` when the demo is built, and the document whose extraction failed. Fictional throughout;
see `demo_cast`.

The waiting files are written into `incoming/` by `demo._loose_ends` and, in the app's sample, are
processed for real with these answers when the reporter adds them: a third change order on the
Pier 9 contract and the notes of a site meeting where the dredging subcontractor asks to be paid
by the City directly, a month after the payment register shows the City already had. The failed
document stays failed: its text is illegible, so a retry fails again.

Each entry has the shape of `demo_docs_a`'s, plus `kind="docx"` (`paras` and `table` are what
`demo_pdf.write_docx` writes, and `md` the text pre-processing would read from it)."""

from watchdog.gui.demo_cast import F, R, REQ

WAITING = []

# ── Change Order 3, March 3, 2023 ───────────────────────────────────────────────────────────
WAITING.append(dict(
    file="pier-9-change-order-3.pdf", kind="pdf",
    title="Change Order 3 - Contract C-2022-041, Pier 9 Marine Servicing Works", dtype="Change Order",
    date="2023-03-03", skill="government-contracts.md",
    source="City of Port Calder Procurement and Real Property, contract disclosure", obtained="2023-05-19",
    author="City of Port Calder", footer="Change Order 3  -  Page {n} of {total}",
    morgue="northgate-civil-works",
    summary=("Change Order 3 to the Pier 9 marine servicing contract, dated March 3, 2023, adds $1,180,000 "
             "for additional quay-wall piling that the Contract Administrator required after a November 2022 "
             "survey. The amount is charged against the contract's mobilization and contingency allowance. "
             "Leonard Pike approved it alone, as the contract allows."),
    scratch=("- Third change order: the first two have not been seen.\n"
             "- $1,180,000 drawn from the $3,740,000 allowance on one signature.\n"
             "- No reference to Council."),
    pages=[
        [("letterhead", "City of Port Calder", "Procurement and Real Property Division"),
         ("title", "Change Order 3 - Contract C-2022-041"),
         ("kv", [("Contract", "C-2022-041, Pier 9 Marine Servicing Works"),
                 ("Contractor", "Northgate Civil Works Ltd."), ("Date", "March 3, 2023"),
                 ("Amount", "$1,180,000")]),
         ("p", "Additional quay-wall piling required by the Contract Administrator following the November "
               "2022 survey. Charged against the mobilization and contingency allowance."),
         ("p", "Approved: Leonard Pike, Director of Procurement and Real Property.")],
    ],
    ents=["city-of-port-calder", "northgate-civil-works", "pier-9", "leonard-pike"],
    facts=[
        F(1, "Change Order 3 to Contract C-2022-041, dated March 3, 2023, adds $1,180,000 to the Pier 9 works "
             "for additional quay-wall piling.", ["northgate-civil-works", "pier-9", "city-of-port-calder"],
          date="2023-03-03"),
        F(1, "The additional piling was required by the Contract Administrator after a survey in November "
             "2022.", ["pier-9", "northgate-civil-works"]),
        F(1, "The $1,180,000 of Change Order 3 is charged against the contract's mobilization and contingency "
             "allowance.", ["northgate-civil-works", "city-of-port-calder"]),
        F(1, "Leonard Pike, Director of Procurement and Real Property, approved Change Order 3 on his own "
             "signature.", ["leonard-pike", "northgate-civil-works"]),
    ],
    roles=[
        R("leonard-pike", "Director of Procurement and Real Property of", "city-of-port-calder", 1),
    ],
    requests=[REQ("Change orders", "Change Orders 1 and 2 to Contract C-2022-041, and the running total charged "
                  "against its mobilization and contingency allowance",
                  "This is the third change order; what the first two added to the $52,340,000 price is not "
                  "on file", "City of Port Calder Procurement and Real Property")],
))

# ── Site meeting notes, September 14, 2022 ──────────────────────────────────────────────────
_NOTES_TITLE = "Pier 9 site meeting notes, September 14, 2022"
_NOTES_PARAS = [
    "Attendees: Harbourline Engineering, Northgate Civil Works, Tideway Marine Services, City staff.",
    "Dredging 62 percent complete. Tideway asks that its next invoice go to the City directly.",
    "Action: Contract Administrator to confirm whether direct payment is permitted.",
]
_NOTES_TABLE = [["Item", "Owner", "Due"], ["Confirm direct payment of Tideway invoices", "Harbourline", "2022-09-21"],
                ["Updated dredging schedule", "Tideway", "2022-09-28"]]

WAITING.append(dict(
    file="site-meeting-notes-2022-09-14.docx", kind="docx",
    title=_NOTES_TITLE, paras=_NOTES_PARAS, table=_NOTES_TABLE,
    md=["# " + _NOTES_TITLE + "\n\n" + "\n\n".join(_NOTES_PARAS) + "\n\n"
        + "\n".join("| " + " | ".join(row) + " |" + ("\n|---|---|---|" if i == 0 else "")
                    for i, row in enumerate(_NOTES_TABLE))],
    dtype="Meeting Notes", date="2022-09-14", skill="municipal-records.md",
    source="City of Port Calder Public Works, released under access request AR-2023-031",
    obtained="2023-04-27", morgue="tideway-marine-services",
    summary=("Notes of a Pier 9 site meeting on September 14, 2022 attended by the Contract Administrator, "
             "Northgate, its dredging subcontractor Tideway Marine Services and City staff. Dredging was 62 "
             "percent complete. Tideway asked that its next invoice go to the City directly, and Harbourline "
             "Engineering was to confirm by September 21 whether direct payment is permitted."),
    scratch=("- The City had already paid Tideway $1,640,000 directly on August 15, 2022.\n"
             "- Whether direct payment is allowed was still an open question a month later."),
    ents=["pier-9", "tideway-marine-services", "northgate-civil-works", "harbourline-engineering",
          "city-of-port-calder"],
    facts=[
        F(1, "At a Pier 9 site meeting on September 14, 2022, dredging of the approach was reported 62 "
             "percent complete.", ["pier-9", "tideway-marine-services"], date="2022-09-14"),
        F(1, "Tideway Marine Services asked that its next invoice go to the City directly.",
          ["tideway-marine-services", "city-of-port-calder"]),
        F(1, "Harbourline Engineering was to confirm by September 21, 2022 whether direct payment of "
             "Tideway's invoices is permitted.", ["harbourline-engineering", "tideway-marine-services"],
          date="2022-09-21"),
        F(1, "Harbourline Engineering, Northgate Civil Works, Tideway Marine Services and City staff "
             "attended the September 14, 2022 site meeting.",
          ["harbourline-engineering", "northgate-civil-works", "tideway-marine-services", "city-of-port-calder"]),
    ],
    roles=[],
))

# The document whose extraction failed when the demo was built: its scan is illegible, so the
# prepared answer is a failure too, and a retry puts it back where it was.
FAILED = dict(
    file="scanned-memo-illegible.pdf", kind="failed", skill="general-records.md", fails=True,
    md=["Ihe C1ty of P0rt Ca1der  --  rnerno  --  [illegible]", "[illegible]"],
)
