"""Prepared Ask Claude conversations for the sample investigation (D309).

Ask Claude can only run a real model, so it is refused in the sample (D307). To show a new user
what it does without a model call, the sample offers a few questions, each with a conversation
prepared in advance and played back in the real chat screen: the question, Claude's tool calls
with their results, and an answer in Markdown that cites the sample's facts with the same
`[[documents/<slug>#^f-…|p. 4]]` links a real session writes.

Nothing here is canned text with hard-coded ids. Each answer names its facts by document and a
phrase of the fact, and is written out when it is asked for, from the sample's own registries:
every citation is the link `citations.link` makes for a fact that exists, an answer that would
cite a missing fact raises `MissingFact` instead of showing "source not found", and
`tests/test_sample_conversations.py` checks every answer against a freshly built sample, before
and after its two waiting files are added. Where those files change what the records say (a
change order, a site meeting about paying Tideway), the answer reads what is there.

A conversation is a list of events in the shape the chat stream emits (`chat.message`,
`chat.delta`, `chat.status`, `chat.permission`), each with the delay before it, so the app plays
it through the same code that shows a live session. One conversation writes: it stops at the
permission card, and the reporter's answer is applied here, through the same session tool a real
session would call (`SessionTools.watchlist_add`), before the rest is returned. No Claude Agent
SDK, client or model is involved anywhere in this module.
"""

from __future__ import annotations

import datetime
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

PREFIX = "sample-"        # the session id a played-back conversation carries


class MissingFact(LookupError):
    """An answer cites a fact the sample does not hold (or a phrase that names several)."""


def _now() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


class Facts:
    """The sample's facts, found by document note slug and a phrase of the fact's text."""

    def __init__(self, vault: Path):
        from watchdog.pipeline.entity_facts import FactIndex
        self.vault = Path(vault)
        self.index = FactIndex(self.vault)

    def find(self, doc: str, phrase: str) -> dict | None:
        sha = self.index.sha_for_note(f"documents/{doc}")
        if not sha:
            return None
        want = phrase.lower()
        hits = [f for f in self.index.document_facts(sha) if want in (f.get("fact") or "").lower()]
        if len(hits) > 1:
            raise MissingFact(f"“{phrase}” names {len(hits)} facts in documents/{doc}")
        return hits[0] if hits and hits[0].get("note") else None

    def has(self, doc: str, phrase: str) -> bool:
        return self.find(doc, phrase) is not None

    def get(self, doc: str, phrase: str) -> dict:
        fact = self.find(doc, phrase)
        if fact is None:
            raise MissingFact(f"no fact in documents/{doc} says “{phrase}”")
        return fact

    def cite(self, doc: str, phrase: str) -> str:
        """The citation link a session writes for this fact (`[[documents/…#^f-…|p. 2]]`)."""
        from watchdog.pipeline import citations
        return f"({citations.link(self.get(doc, phrase))})"


class Script:
    """Builds the events of one played-back turn, each with the delay before it."""

    WORDS_PER_CHUNK = 3
    CHUNK_MS = 24

    def __init__(self, session: str):
        self.session = session
        self.events: list[dict] = []
        self._n = 0

    def _id(self) -> str:
        self._n += 1
        return f"{self.session}-{self._n}"

    def _emit(self, after_ms: int, event: str, data: dict) -> None:
        self.events.append({"after_ms": after_ms, "event": event, "data": {"session": self.session, **data}})

    def _message(self, after_ms: int, msg: dict) -> None:
        self._emit(after_ms, "chat.message", {"message": msg})

    def status(self, state: str, after_ms: int = 0) -> None:
        self._emit(after_ms, "chat.status", {"state": state, "detail": None, "cost_usd": None})

    def user(self, text: str) -> None:
        self._message(0, {"id": self._id(), "role": "user", "text": text, "ts": _now()})
        self.status("thinking", 150)

    def tool_call(self, name: str, tool_input: dict, after_ms: int = 600, mid: str | None = None) -> dict:
        msg = {"id": mid or self._id(), "role": "tool", "text": "", "ts": _now(),
               "tool": {"name": name, "input": tool_input, "result": None, "is_error": False}}
        self._message(after_ms, msg)
        return msg

    def tool_result(self, msg: dict, result: str, is_error: bool = False, after_ms: int = 700) -> None:
        done = {**msg, "tool": {**msg["tool"], "result": result, "is_error": is_error}}
        self._message(after_ms, done)

    def tool(self, name: str, tool_input: dict, result: str, run_ms: int = 700) -> None:
        self.tool_result(self.tool_call(name, tool_input), result, after_ms=run_ms)

    def say(self, text: str, after_ms: int = 400) -> None:
        """Claude's text, streamed in a few words at a time, then the finished message."""
        mid = self._id()
        words = text.split(" ")
        for i in range(0, len(words), self.WORDS_PER_CHUNK):
            chunk = " ".join(words[i:i + self.WORDS_PER_CHUNK])
            if i + self.WORDS_PER_CHUNK < len(words):
                chunk += " "
            self._emit(after_ms if i == 0 else self.CHUNK_MS, "chat.delta",
                       {"message_id": mid, "text": chunk})
        self._message(60, {"id": mid, "role": "assistant", "text": text, "ts": _now()})

    def permission(self, vault: Path, request_id: str, tool: str, tool_input: dict) -> None:
        """The permission card, worded by the same function a live session's card uses."""
        from watchdog.gui.chat import permission_request
        ask = permission_request(vault, tool, tool_input)
        self._emit(500, "chat.permission", {
            "request_id": request_id, "tool": tool, "input": tool_input,
            "description": ask["description"], "title": ask["title"], "scope": ask["scope"],
            "once_only": ask["once_only"], "note": ask["note"]})

    def idle(self) -> None:
        self.status("idle", 120)


@dataclass
class Turn:
    vault: Path
    facts: Facts
    script: Script


@dataclass
class Conversation:
    id: str
    question: str
    blurb: str          # what it shows, one line, for the button
    build: Callable[[Turn], None]
    # For a conversation that stops at a permission card: the tool call it asks about, and what
    # follows the reporter's answer (`allow`, plus the turn).
    asks: tuple[str, dict] | None = None
    after: Callable[[Turn, bool], None] | None = None


# ── tool results ─────────────────────────────────────────────────────────────────────────────

def _read(vault: Path, rel: str, lines: int = 34) -> tuple[dict, str]:
    """A Read call on a note and what Claude Code's Read returns: the file's first lines,
    numbered."""
    path = Path(vault) / rel
    text = path.read_text(encoding="utf-8").splitlines()
    shown = [f"{i:>6}\t{line}" for i, line in enumerate(text[:lines], 1)]
    if len(text) > lines:
        shown.append(f"… ({len(text) - lines} more lines)")
    return {"file_path": str(path)}, "\n".join(shown)


def _search(facts: Facts, query: str, picks: list[tuple[str, str]]) -> tuple[dict, str]:
    """A search call and a result in the search tool's shape: each passage the source wording,
    with the facts recorded on it and their ready-made `cite` links."""
    from watchdog.pipeline import citations
    passages = []
    for doc, phrase in picks:
        f = facts.get(doc, phrase)
        passages.append({
            "filename": (facts.index.documents.get(f["sha"]) or {}).get("filename"),
            "page": f.get("page"),
            "text": f.get("passage") if f.get("passage_method") == "matched" and f.get("passage") else f["fact"],
            "facts": [{"fact": f["fact"], "cite": citations.link(f),
                       "mark": (f.get("mark") or {}).get("status")}],
        })
    return {"query": query}, json.dumps({"query": query, "passages": passages}, ensure_ascii=False, indent=1)


def _search_tool(t: Turn, query: str, picks: list[tuple[str, str]]) -> None:
    tool_input, result = _search(t.facts, query, picks)
    t.script.tool("mcp__watchdog__search", tool_input, result, run_ms=900)


def _read_tool(t: Turn, rel: str) -> None:
    tool_input, result = _read(t.vault, rel)
    t.script.tool("Read", tool_input, result, run_ms=500)


def _waiting(vault: Path) -> int:
    """Files added to the investigation's folder but not in its notes yet: in `incoming/`, or
    queued for processing."""
    from watchdog.vault import count_awaiting_processing
    from watchdog.vault_paths import incoming_dir
    inc = incoming_dir(vault)
    files = [p for p in inc.iterdir() if p.is_file() and not p.name.startswith(".")] if inc.is_dir() else []
    return len(files) + count_awaiting_processing(vault)


# ── the conversations ────────────────────────────────────────────────────────────────────────

CONTRACT = "contract-c-2022-041-pier-9-servicing"
REGISTER = "capital-payment-register-2022"
AUDIT = "city-auditor-procurement-review-ar-2023-04"
MINUTES_APR = "council-minutes-2022-04-26"
MINUTES_FEB = "council-minutes-2022-02-08"
PARCEL = "parcel-register-14-dockside-road"
PROFILE = "7714882-holdings-corporate-profile"
FOI = "foi-response-lot-14-appraisal"
LOBBY = "lobbyist-registration-lr-2021-0377"
HARBOUR = "harbour-authority-annual-report-2022"
CHANGE_ORDER = "pier-9-change-order-3"          # one of the two waiting files
SITE_NOTES = "site-meeting-notes-2022-09-14"    # the other


def _change_orders(t: Turn) -> None:
    c, s = t.facts.cite, t.script
    s.say("I'll search the documents for change orders on the Pier 9 contract, then check what "
          "the records say about who may sign one.", after_ms=500)
    have_order = t.facts.has(CHANGE_ORDER, "approved Change Order 3")
    picks = [(CONTRACT, "require only a written change order"), (REGISTER, "delegated authority")]
    if have_order:
        picks.insert(0, (CHANGE_ORDER, "approved Change Order 3"))
    _search_tool(t, "Pier 9 contract change order approval", picks)
    _read_tool(t, "entities/person/leonard-pike.md")

    authority = (
        f"- **His authority.** The contract itself gives it to him: payments above the Total Contract "
        f"Price need only a written change order signed by the Director of Procurement and Real "
        f"Property {c(CONTRACT, 'require only a written change order')}. Every payment in the 2022 capital "
        f"register was likewise approved under the Procurement Director's delegated authority, and none "
        f"was reported to Council as a separate item {c(REGISTER, 'delegated authority')}.\n"
        f"- **What Council approved.** Council awarded the contract at a value not to exceed $48,600,000 "
        f"{c(MINUTES_APR, 'Council voted 7 to 3')}. The contract as signed is for $52,340,000, which adds a "
        f"$3,740,000 mobilization and contingency allowance {c(CONTRACT, 'The Total Contract Price is')}, "
        f"and the City Auditor found the difference was never reported to Council "
        f"{c(AUDIT, 'exceeds Council')}.\n")
    if have_order:
        text = (
            "Leonard Pike, the City's Director of Procurement and Real Property, approved the one change "
            "order in the records, on his own signature. The contract lets him do that without going back "
            "to Council.\n\n"
            f"- **The change order.** Change Order 3, dated March 3, 2023, adds $1,180,000 for additional "
            f"quay-wall piling {c(CHANGE_ORDER, 'adds $1,180,000')}. Pike approved it alone "
            f"{c(CHANGE_ORDER, 'approved Change Order 3')}, and the amount is charged against the contract's "
            f"mobilization and contingency allowance {c(CHANGE_ORDER, 'charged against')}.\n"
            + authority.replace("**His authority.**", "**On whose authority.**") +
            "\nSo Change Order 3 spends part of an allowance Council never approved, on one signature.\n\n"
            "**Not in the records.** This is the third change order. Change Orders 1 and 2 are not in the "
            "investigation, so what they added, and who signed them, is unknown. They are worth requesting "
            "from the City's Procurement and Real Property division.")
    else:
        waiting = _waiting(t.vault)
        text = (
            "The investigation holds no change order to the Pier 9 contract, so the records cannot say who "
            "approved one. They do show who could, and that Council would not have been asked.\n\n"
            + authority.replace("**His authority.**", "**Who may sign one.**")
            .replace("gives it to him", "gives that power to one official") +
            "\nThat official is Leonard Pike: the lobbyist registry names him as Director of Procurement and "
            f"Real Property when it records a meeting with Meridian's lobbyist about the Pier 9 time-critical "
            f"exception {c(LOBBY, 'meeting with Leonard Pike')}.\n\n"
            + (f"**Worth knowing.** {waiting} file{'s are' if waiting != 1 else ' is'} still waiting in "
               "`incoming/` and not in the notes yet. Add them under **Add documents** and ask again: one "
               "may bear on this." if waiting else
               "**Not in the records.** No change order has been added. It is worth requesting the contract's "
               "change orders, and the running total charged against its allowance, from the City."))
    s.say(text)
    s.idle()


def _tideway(t: Turn) -> None:
    c, s = t.facts.cite, t.script
    s.say("I'll start with Tideway's entity note, then search the documents for payments to it.",
          after_ms=500)
    _read_tool(t, "entities/organization/tideway-marine-services.md")
    notes = t.facts.has(SITE_NOTES, "next invoice go to the City directly")
    picks = [(REGISTER, "directly to Tideway"), (CONTRACT, "separate subcontract"),
             (HARBOUR, "no payments to Tideway")]
    if notes:
        picks.append((SITE_NOTES, "next invoice go to the City directly"))
    _search_tool(t, "payment to Tideway Marine Services", picks)
    text = (
        "The City paid Tideway Marine Services directly, although Tideway worked for Northgate, not for "
        "the City.\n\n"
        f"- **Tideway was a subcontractor.** Northgate engaged Tideway to dredge with the Dredge Calder "
        f"Princess under a separate subcontract that is not part of the City contract "
        f"{c(CONTRACT, 'separate subcontract')}.\n"
        f"- **The City paid it anyway.** On August 15, 2022 the City paid $1,640,000 directly to Tideway for "
        f"dredging the Pier 9 approach {c(REGISTER, 'directly to Tideway')}. Like every payment in that "
        f"register, it was approved under the Procurement Director's delegated authority and not reported "
        f"to Council as a separate item {c(REGISTER, 'delegated authority')}.\n"
        f"- **Not through the usual check.** Harbourline Engineering, the Contract Administrator, certifies "
        f"Northgate's progress claims {c(CONTRACT, 'certifies Northgate')}. No recorded fact shows it "
        f"certifying the payment to Tideway.\n"
        f"- **Not the Harbour Authority.** The Authority made no payments to Tideway "
        f"{c(HARBOUR, 'no payments to Tideway')}.\n")
    if notes:
        text += (
            f"- **A month later, still an open question.** At a site meeting on September 14, 2022, Tideway "
            f"asked that its next invoice go to the City directly {c(SITE_NOTES, 'next invoice go to the City directly')}, "
            f"and Harbourline was to confirm by September 21 whether direct payment is permitted "
            f"{c(SITE_NOTES, 'was to confirm by September 21')}. Watchdog has recorded this as a "
            f"contradiction on Tideway's page: the register shows the City had already paid it directly.\n")
    text += (
        "\n**Not in the records:** the subcontract, Tideway's invoice, and any decision allowing the City to "
        "pay a subcontractor directly. Those three documents would show whether the payment was "
        "authorized, and by whom.")
    s.say(text)
    s.idle()


def _dockside(t: Turn) -> None:
    c, s = t.facts.cite, t.script
    s.say("I'll read the note for 14 Dockside Road, then search the documents for the sale.", after_ms=500)
    _read_tool(t, "entities/place/lot-14-dockside-road.md")
    _search_tool(t, "14 Dockside Road purchase price transfer", [
        (PARCEL, "stated consideration of $4,350,000"), (MINUTES_FEB, "Council voted 8 to 3"),
        (FOI, "market value of 14 Dockside Road")])
    disputed = (t.facts.get(REGISTER, "paid $4,350,000 to 7714882").get("mark") or {}).get("status") == "disputed"
    text = (
        "Here is the purchase in order, from the records in this investigation.\n\n"
        "| Date | What happened |\n|---|---|\n"
        f"| 1987 to April 22, 2021 | The Estate of Albert Kessler owns the property {c(PARCEL, 'held 14 Dockside Road from 1987')} |\n"
        f"| March 9, 2021 | 7714882 Holdings Ltd. is incorporated, with Marcus Teague as sole director {c(PROFILE, 'was incorporated on March 9, 2021')} |\n"
        f"| April 22, 2021 | The estate sells the property to 7714882 Holdings for $1,150,000 {c(PARCEL, 'transferred 14 Dockside Road to 7714882 Holdings Ltd. for $1,150,000')} |\n"
        f"| November 17, 2021 | Meridian's lobbyist reports a meeting with Councillor Whitcombe about the acquisition {c(LOBBY, 'meeting with Councillor Dana Whitcombe')} |\n"
        f"| December 10, 2021 | The City's appraisal values it at $1,420,000 {c(FOI, 'market value of 14 Dockside Road')} |\n"
        f"| January 15, 2022 | Teague resigns as director of 7714882 Holdings {c(PROFILE, 'Notice of Change filed January 20')} |\n"
        f"| January 24, 2022 | A staff e-mail mentions a second valuation; the City could not find it {c(FOI, 'second valuation')} |\n"
        f"| February 8, 2022 | Council votes 8 to 3 to buy for $3,900,000 {c(MINUTES_FEB, 'Council voted 8 to 3')}, on a report saying an independent appraisal supported that price {c(MINUTES_FEB, 'Report CR-2022-011 recommended')} |\n"
        f"| February 28, 2022 | The transfer to the City registers at $4,350,000 {c(PARCEL, 'stated consideration of $4,350,000')}, signed for the vendor by Teague as director {c(PARCEL, 'executed for 7714882 Holdings Ltd. by Marcus Teague')} |\n"
        f"| March 4, 2022 | The payment register shows $4,350,000 paid to 7714882 Holdings {c(REGISTER, 'paid $4,350,000 to 7714882')}"
        + (", a fact you marked disputed" if disputed else "") + " |\n"
        f"| June 12, 2023 | The City Auditor cannot reconcile $4,350,000 with the $3,900,000 Council approved {c(AUDIT, 'could not reconcile')} |\n\n"
        "**Three things to check.**\n\n"
        "1. **The price.** Council approved $3,900,000; the land registry and the payment register say "
        "$4,350,000. Watchdog has flagged this as a contradiction on the property's page.\n"
        "2. **Who signed.** Teague signed the transfer as a director six weeks after the corporate registry "
        "records his resignation. That is flagged as a contradiction on his page.\n"
        f"3. **The appraisal.** The only appraisal on file values the property at $1,420,000, and the Clerk "
        f"says the $3,900,000 in the minutes is not derived from it {c(FOI, 'not derived from the released appraisal')}. "
        f"The second valuation the staff e-mail mentions is the obvious document to request.")
    s.say(text)
    s.idle()


WATCH_TERM = "Tideway Marine Services"
_WATCH_INPUT = {"terms": [WATCH_TERM]}


def _watch_tool_id(s: Script) -> str:
    return f"{s.session}-tool"


def _watch(t: Turn) -> None:
    s = t.script
    s.say(f"I'll add **{WATCH_TERM}** to the watch list, so Watchdog flags the name in documents you add "
          "later.", after_ms=500)
    s.tool_call("mcp__watchdog__watchlist_add", _WATCH_INPUT, mid=_watch_tool_id(s))


def _watch_after(t: Turn, allow: bool) -> None:
    """What follows the permission card: the change through the session tool when allowed."""
    s = t.script
    msg = {"id": _watch_tool_id(s), "role": "tool", "text": "", "ts": _now(),
           "tool": {"name": "mcp__watchdog__watchlist_add", "input": _WATCH_INPUT, "result": None,
                    "is_error": False}}
    if not allow:
        s.tool_result(msg, "The user declined this action.", is_error=True, after_ms=200)
        s.say("I haven't changed the watch list. You can add the name yourself at any time under "
              "**Review → Watch list**.")
        s.idle()
        return
    from watchdog.session_tools import SessionTools, ToolError
    try:
        result = SessionTools(t.vault).watchlist_add(list(_WATCH_INPUT["terms"]))
    except ToolError as e:
        s.tool_result(msg, str(e), is_error=True, after_ms=300)
        s.say(f"I couldn't change the watch list: {e}")
        s.idle()
        return
    s.tool_result(msg, result, after_ms=300)
    added = json.loads(result).get("added") or []
    if added:
        s.say(f"Done: **{WATCH_TERM}** is on the watch list. Watchdog will flag it whenever it appears in a "
              "document you add from now on, and the hits wait under **Review → Watch list**. The name "
              "is already in documents here; **Check every document now**, on that tab, finds those too.\n\n"
              "The change is recorded in the watch list's history, so you can see what was there before.")
    else:
        s.say(f"**{WATCH_TERM}** is already on the watch list, so nothing changed. Watchdog flags it "
              "whenever it appears in a document you add.")
    s.idle()


CONVERSATIONS: list[Conversation] = [
    Conversation("change-orders", "Who approved the change orders to the Pier 9 contract, and on whose authority?",
                 "Follows the authority through the contract, the payments and the audit.", _change_orders),
    Conversation("tideway", "What do the records say about how Tideway was paid?",
                 "Brings together a payment, a subcontract and what is missing.", _tideway),
    Conversation("dockside", "Draft a timeline of the 14 Dockside Road purchase.",
                 "A dated timeline with a citation for each line, and what to check.", _dockside),
    Conversation("watch", f"Add {WATCH_TERM} to my watch list.",
                 "Shows how Claude asks before it changes the investigation.", _watch,
                 asks=("mcp__watchdog__watchlist_add", _WATCH_INPUT), after=_watch_after),
]


def questions() -> list[dict]:
    return [{"id": c.id, "question": c.question, "blurb": c.blurb, "writes": c.asks is not None}
            for c in CONVERSATIONS]


def _conversation(cid: str) -> Conversation:
    for c in CONVERSATIONS:
        if c.id == cid:
            return c
    raise KeyError(cid)


def _session(cid: str) -> str:
    return PREFIX + cid


def _request_id(cid: str) -> str:
    return f"{_session(cid)}-ask"


def play(vault: Path, cid: str) -> dict:
    """The events of conversation `cid` in the sample at `vault`: `{session, events, waiting}`,
    `waiting` the permission request it stops at (None when it plays to the end). Raises KeyError
    for an unknown conversation and MissingFact when the sample lacks a fact it cites."""
    conv = _conversation(cid)
    turn = Turn(Path(vault), Facts(vault), Script(_session(cid)))
    turn.script.user(conv.question)
    conv.build(turn)
    waiting = None
    if conv.asks:
        waiting = _request_id(cid)
        tool, tool_input = conv.asks
        turn.script.permission(turn.vault, waiting, tool, tool_input)
    return {"session": turn.script.session, "events": turn.script.events, "waiting": waiting}


def answer(vault: Path, cid: str, request_id: str, allow: bool) -> dict:
    """The rest of a conversation that stopped at a permission card, after the reporter's answer;
    an allowed change is made here, through the session tool. `{events}`."""
    conv = _conversation(cid)
    if conv.after is None or request_id != _request_id(cid):
        raise KeyError(request_id)
    turn = Turn(Path(vault), Facts(vault), Script(_session(cid)))
    conv.after(turn, bool(allow))
    return {"events": turn.script.events}
