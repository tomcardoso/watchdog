"""The fictional cast of the demo vault: every person, company, public body, place, asset and
proceeding named in `demo_docs_a`/`demo_docs_b`, plus the small builders those modules share.

Everything here is invented. Port Calder is not a real city, and no entity below corresponds to a
real person, company, agency, address or court matter; the story is a made-up municipal
procurement scandal written to give the desktop app a realistic, populated vault to show.
"""

from __future__ import annotations

# id -> (display name, canonical type, aliases). A document's extraction lists an alias only when
# the alias actually appears in that document's text (see `demo.entity_list`).
ENT: dict[str, tuple[str, str, list[str]]] = {
    # people
    "dana-whitcombe": ("Dana Whitcombe", "person",
                       ["Councillor Whitcombe", "Cllr. D. Whitcombe", "D. Whitcombe"]),
    "marcus-teague": ("Marcus Teague", "person", ["M. Teague", "Marcus J. Teague"]),
    "helen-okafor-reyes": ("Helen Okafor-Reyes", "person", ["H. Okafor-Reyes", "Ms. Okafor-Reyes"]),
    "priya-raman": ("Priya Raman", "person", ["City Auditor Raman"]),
    "robert-delacroix": ("Robert Delacroix", "person", ["Mayor Delacroix", "Mayor Robert Delacroix"]),
    "anita-sandhu": ("Anita Sandhu", "person", ["City Clerk Sandhu"]),
    "tomasz-wieczorek": ("Tomasz Wieczorek", "person", ["Tom Wieczorek", "T. Wieczorek"]),
    "colleen-brandt": ("Colleen Brandt", "person", ["C. Brandt"]),
    "ingrid-halvorsen": ("Ingrid Halvorsen", "person", ["Justice Halvorsen", "Justice I. Halvorsen"]),
    "samir-haddad": ("Samir Haddad", "person", ["S. Haddad"]),
    "leonard-pike": ("Leonard Pike", "person", ["L. Pike", "Mr. Pike", "Leonard Pyke"]),
    "grace-liu": ("Grace Liu", "person", ["G. Liu"]),
    "owen-macaskill": ("Owen Macaskill", "person", ["O. Macaskill"]),
    "elaine-fortier": ("Elaine Fortier", "person", ["Integrity Commissioner Fortier"]),
    "victor-anand": ("Victor Anand", "person", ["Councillor Anand"]),
    "nadia-ferreira": ("Nadia Ferreira", "person", ["Councillor Ferreira"]),
    "ben-oyelaran": ("Ben Oyelaran", "person", ["Councillor Oyelaran"]),
    "marisol-fernandes": ("Marisol Fernandes", "person", ["M. Fernandes"]),
    # organizations
    "meridian-shoreline-developments": (
        "Meridian Shoreline Developments Inc.", "organization",
        ["Meridian Shoreline Developments", "Meridian Shoreline", "MSD"]),
    "northgate-civil-works": ("Northgate Civil Works Ltd.", "organization",
                              ["Northgate Civil Works", "Northgate"]),
    "7714882-holdings-ltd": ("7714882 Holdings Ltd.", "organization", ["7714882 Holdings"]),
    "strathmore-public-affairs": ("Strathmore Public Affairs Inc.", "organization",
                                  ["Strathmore Public Affairs", "Strathmore"]),
    "macaskill-rowe-appraisals": ("Macaskill & Rowe Appraisals Ltd.", "organization",
                                  ["Macaskill & Rowe"]),
    "calder-citizens-for-open-government": ("Calder Citizens for Open Government", "organization",
                                            ["Calder Citizens", "CCOG"]),
    "harbourline-engineering": ("Harbourline Engineering Inc.", "organization",
                                ["Harbourline Engineering"]),
    "tideway-marine-services": ("Tideway Marine Services Ltd.", "organization",
                                ["Tideway Marine Services", "Tideway Marine"]),
    "first-maritime-credit-union": ("First Maritime Credit Union", "organization",
                                    ["First Maritime"]),
    "kessler-estate": ("Estate of Albert Kessler", "organization", ["Kessler Estate"]),
    "blackwater-capital-partners": ("Blackwater Capital Partners LP", "organization",
                                    ["Blackwater Capital"]),
    # public bodies
    "city-of-port-calder": ("City of Port Calder", "public-body", ["the City", "Port Calder"]),
    "port-calder-city-council": ("Port Calder City Council", "public-body",
                                 ["City Council", "Council"]),
    "planning-and-procurement-committee": ("Planning and Procurement Committee", "public-body",
                                           ["the Committee"]),
    "planning-procurement-committee": ("Planning & Procurement Committee", "public-body", []),
    "port-calder-land-registry": ("Port Calder Land Registry", "public-body", []),
    "port-calder-harbour-authority": ("Port Calder Harbour Authority", "public-body",
                                      ["Harbour Authority", "the Authority"]),
    "office-of-the-city-auditor": ("Office of the City Auditor", "public-body",
                                   ["City Auditor's Office"]),
    "ministry-of-municipal-affairs": ("Provincial Ministry of Municipal Affairs", "public-body",
                                      ["the Ministry"]),
    "port-calder-superior-court": ("Port Calder Superior Court", "public-body", ["the Court"]),
    "port-calder-land-registry-office": ("Port Calder Land Registry Office", "public-body",
                                         ["Land Registry Office"]),
    "office-of-the-integrity-commissioner": ("Office of the Integrity Commissioner", "public-body",
                                             ["Integrity Commissioner's Office"]),
    "office-of-the-lobbyist-registrar": ("Office of the Lobbyist Registrar", "public-body",
                                         ["Lobbyist Registrar"]),
    # places
    "lot-14-dockside-road": ("14 Dockside Road", "place", ["Lot 14", "Lot 14, Plan DP-2217"]),
    "pier-9": ("Pier 9", "place", ["Pier Nine"]),
    "harbourfront-lands": ("Harbourfront Lands", "place", ["the Harbourfront"]),
    "410-wharf-street": ("410 Wharf Street", "place", ["410 Wharf St."]),
    "calder-harbour": ("Calder Harbour", "place", ["Port Calder Harbour"]),
    "port-calder-city-hall": ("Port Calder City Hall", "place", ["City Hall"]),
    "88-gull-lane": ("88 Gull Lane", "place", []),
    # assets
    "dredge-calder-princess": ("Dredge Calder Princess", "asset", ["Calder Princess"]),
    "performance-bond-pb-88213": ("Performance Bond PB-88213", "asset", []),
    "charge-pc-418803": ("Charge PC-418803", "asset", ["First Maritime mortgage"]),
    "meridianshoreline-example": ("meridianshoreline.example", "asset", []),
    # proceedings
    "citizens-v-city-judicial-review": (
        "Calder Citizens for Open Government v. City of Port Calder", "proceeding",
        ["Citizens v. City", "Court File JR-2022-0481"]),
    "integrity-commissioner-inquiry-ic-2022-07": (
        "Integrity Commissioner Inquiry IC-2022-07", "proceeding", ["IC-2022-07"]),
    "city-auditor-procurement-review-2023": (
        "City Auditor Procurement Review (Pier 9)", "proceeding", ["Pier 9 procurement review"]),
}


def F(page, text, ents, q=None, date=None, inferred=False):
    """A key fact: what it says, where (1-based page), which entity ids it is about, the opening
    words of a source sentence (`q`, resolved to a full quote by post-flight), and an optional
    date that also places it on the timeline."""
    return {"page": page, "text": text, "ents": list(ents), "q": q, "date": date,
            "inferred": inferred}


def R(src, relationship, target, page, date_range=None, inferred=False, tname=None, ttype=None):
    """A relationship from `src` to `target` stated on `page`. `tname`/`ttype` name a target the
    document mentions but that has no entity record of its own (a lead: named but never profiled)."""
    return {"src": src, "rel": relationship, "tgt": target, "page": page,
            "date_range": date_range, "inferred": inferred, "tname": tname, "ttype": ttype}


def REQ(kind, what, why, source=None):
    """A document a reporter could go and get."""
    out = {"type": kind, "what": what, "why_it_matters": why}
    if source:
        out["likely_source"] = source
    return out
