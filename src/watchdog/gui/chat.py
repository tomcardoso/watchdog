"""Claude Code sessions inside the app, run through the Claude Agent SDK in the vault's folder.

Ask Claude, setting the context and web research run here: the session
starts in the vault with `setting_sources=["project"]`, so the vault's own `.claude/` settings,
`CLAUDE.md` and `/watchdog-*` commands apply, and the first prompt wraps a question for the query skill. Watchdog's own tools come from an in-process MCP server (`watchdog/session_tools.py`),
its hooks are Python callbacks here, and the session has no shell (D299).

Every session runs on one asyncio event loop in a daemon thread; RPC handlers (which run on a
thread pool) submit coroutines with `run_coroutine_threadsafe`. Tool permissions are asked of the
app: `can_use_tool` emits `chat.permission` and waits on a future the app's `chat.permission`
answer resolves. Transcripts are saved as JSON under `~/.watchdog/gui/chats/<session>.json` with the
SDK's own session id, so a saved chat can be resumed.
"""

from __future__ import annotations

import asyncio
import datetime
import json
import os
import threading
import uuid
from pathlib import Path

from watchdog.gui import rpc
from watchdog.gui.rpc import RpcError

MODES = ("ask", "context", "research")
# The models a conversation can run on (D313). Always named, never left to Claude Code, whose own
# default can be Opus.
CHAT_MODELS = ("sonnet", "haiku")
DEFAULT_CHAT_MODEL = "sonnet"
_AUTH_HINT = ("Claude is not signed in. Sign in under Settings → Models & keys, then try again.")
_MISSING_HINT = ("Claude, which comes with Watchdog's engine, could not be started. Repair the "
                 "engine under Settings → Setup, then try again.")


def _now() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


def chats_dir() -> Path:
    from watchdog import config
    return config.home() / "gui" / "chats"


def make_client(options):
    """The SDK client for a session. Replaced in tests."""
    from claude_agent_sdk import ClaudeSDKClient
    return ClaudeSDKClient(options)


def session_auth_env(session: "Session") -> dict:
    """The Anthropic key a session in API-key mode runs on: the one this investigation chose, or
    the default (D290), passed as ANTHROPIC_API_KEY. On a subscription nothing is added and
    Claude Code uses its own login. Sessions follow their own billing choice (D318), which can
    differ from the one processing uses. Raises `auth.KeyChoiceError` when the chosen key isn't on
    this computer, before the session starts, rather than billing another account."""
    from watchdog import auth
    a = auth.resolve_auth("anthropic", session.vault, session=True)
    if a.get("mode") != "api-key" or not a.get("key"):
        session.key_label = None
        return {}
    session.key_label = auth.label_for_key("anthropic", a["key"])
    return {"ANTHROPIC_API_KEY": a["key"]}


def build_options(session: "Session", can_use_tool):
    from claude_agent_sdk import ClaudeAgentOptions, HookMatcher
    from watchdog import session_tools
    from watchdog.primer import session_text
    from watchdog.model_catalog import resolve_model_id
    # The primer (D285) goes into the system prompt when the session connects (D299): the Python
    # SDK has no SessionStart hook, and the system prompt, unlike a hook's output, survives
    # compaction.
    system_prompt = {"type": "preset", "preset": "claude_code"}
    primer = session_text(session.vault)
    if primer:
        system_prompt["append"] = primer
    from watchdog.vault import session_settings
    kwargs = dict(cwd=str(session.vault),
                  # The vault's CLAUDE.md and `/watchdog-*` commands load only with the project
                  # source. Its settings file loads too, but the settings below outrank it.
                  setting_sources=["project"],
                  # The session's rules, passed by the app rather than read from the vault, so a
                  # vault's settings file can't add hooks or loosen them (D304).
                  settings=json.dumps(session_settings()),
                  # Only Watchdog's own server: never a vault's `.mcp.json` (D304).
                  strict_mcp_config=True,
                  include_partial_messages=True,
                  # Every call not pre-approved goes to `can_use_tool`, the reporter's card. Named
                  # because Claude Code's own default can be its "auto" mode, where a model of its
                  # own decides instead (D304).
                  permission_mode="default",
                  system_prompt=system_prompt, can_use_tool=can_use_tool,
                  # Watchdog's tools, in this process and bound to this investigation (D299).
                  mcp_servers={session_tools.SERVER: session_tools.sdk_server(session.vault)},
                  # The built-in tools a session has at all (D304): an allowlist, so a tool a later
                  # Claude Code adds stays off until it is reviewed here.
                  tools=list(SESSION_TOOLS),
                  # Only the read-only Watchdog tools run without asking; the four that write go
                  # through `can_use_tool`, the app's permission card (D304). A research session
                  # also searches and reads the web without asking (D344).
                  allowed_tools=[session_tools.tool_name(t) for t in session_tools.READ_TOOLS]
                  + (list(RESEARCH_WEB_TOOLS) if session.mode == "research" else []),
                  disallowed_tools=list(DISALLOWED_TOOLS))
    kwargs["env"] = session_auth_env(session)
    from watchdog.gui.engine_setup import bundled_claude_path
    if bundled_claude_path():
        kwargs["cli_path"] = bundled_claude_path()
    kwargs["model"] = resolve_model_id(session.model if session.model in CHAT_MODELS else DEFAULT_CHAT_MODEL)
    if session.sdk_session_id:
        kwargs["resume"] = session.sdk_session_id
    kwargs["hooks"] = {
        "PreToolUse": [
            HookMatcher(matcher="|".join(_EDIT_TOOLS), hooks=[_vault_only_edits(session.vault)]),
            HookMatcher(matcher=PAGE_NOTES_MATCHER, hooks=[_page_notes("pre")]),
        ],
        "PostToolUse": [HookMatcher(matcher=PAGE_NOTES_MATCHER, hooks=[_page_notes("post")])],
        "UserPromptSubmit": [HookMatcher(hooks=[_prompt_status(session.vault)])],
    }
    return ClaudeAgentOptions(**kwargs)


# The built-in Claude Code tools a session has (D304). Reading and searching the vault, editing its
# session pages (the edit hook below refuses anything else), the web for research (each fetch asks
# unless the research command pre-approves it), a to-do list, and Skill, which a command uses to
# hand over to another (`/watchdog-query` suggests `/watchdog-wiki`).
SESSION_TOOLS = ("Read", "Glob", "Grep", "Write", "Edit", "NotebookEdit", "WebFetch", "WebSearch",
                 "TodoWrite", "Skill")

# Pre-approved in a Web research session only (D344): searching the web and reading pages is the
# whole job there, and a card per host buried the conversation. Elsewhere they still ask. Neither
# can write to the investigation; a fetched page stays untrusted data (I6).
RESEARCH_WEB_TOOLS = ("WebSearch", "WebFetch")

# Belt and braces behind the allowlist: a session has no shell (D299) and nothing that starts other
# agents, schedules prompts, runs workflow scripts or moves the session to another folder (D304),
# whatever a later Claude Code adds by default. Every tool that runs a command goes: Bash,
# PowerShell (Windows), Monitor (a background command) and the two that manage a background shell.
DISALLOWED_TOOLS = ("Bash", "PowerShell", "Monitor", "BashOutput", "KillShell",
                    "Agent", "Task", "Workflow", "CronCreate", "CronDelete", "CronList",
                    "ScheduleWakeup", "EnterWorktree", "ExitWorktree", "SendMessage",
                    "PushNotification", "DesignSync", "ListAgents", "RemoteTrigger")


# ── hooks (D299): Python callbacks in this process, no command and no PATH ──────────────────────

# Around a session's Write, Edit or MultiEdit, the reporter's Notes on a saved page are put aside
# before and put back after if the edit dropped them (D296).
PAGE_NOTES_MATCHER = "Write|Edit|MultiEdit"


def _page_notes(stage: str):
    async def hook(input_data, tool_use_id, context):
        from watchdog.pipeline import page_notes
        out = await asyncio.to_thread(page_notes.run_hook, stage, json.dumps(input_data or {}))
        return json.loads(out) if out else {}
    return hook


def prompt_status(vault: Path) -> str | None:
    """The one-line note added to each prompt while documents wait for a stage, or None."""
    from watchdog.vault import count_awaiting_post, count_awaiting_processing
    waiting, staged = count_awaiting_processing(vault), count_awaiting_post(vault)
    parts = []
    if waiting:
        parts.append(f"{waiting} file(s) waiting for processing")
    if staged:
        parts.append(f"{staged} file(s) processed and waiting for post-processing")
    return ("WATCHDOG: " + "; ".join(parts) + " (the reporter adds documents in the app; they are "
            "not in the vault's notes yet)") if parts else None


def _prompt_status(vault: Path):
    async def hook(input_data, tool_use_id, context):
        try:
            line = await asyncio.to_thread(prompt_status, vault)
        except Exception:  # noqa: BLE001 — the note must never fail a prompt
            line = None
        if not line:
            return {}
        return {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": line}}
    return hook


# Claude Code's file-editing tools and the input key naming the file each one changes.
_EDIT_TOOLS = {"Write": "file_path", "Edit": "file_path", "MultiEdit": "file_path",
               "NotebookEdit": "notebook_path"}


def _vault_relative(vault: Path, raw: str) -> str | None:
    """`raw` (absolute, or relative to the vault) as a vault-relative path with forward slashes,
    symlinks resolved; None when it is outside the vault. "" is the vault folder itself."""
    target = Path(raw) if Path(raw).is_absolute() else Path(vault) / raw
    real_target = Path(os.path.realpath(target))
    root = Path(os.path.realpath(vault))
    if real_target == root:
        return ""
    if root not in real_target.parents:
        return None
    return real_target.relative_to(root).as_posix()


def edit_outside_vault(vault: Path, tool: str, tool_input: dict) -> str | None:
    """Why `tool` may not run, or None. A session edits only its own pages in its own
    investigation folder (`vault.SESSION_WRITABLE`), and only when the user has allowed Watchdog
    there (watchdog/access.py) — whatever the vault's settings or an "Allow for this conversation" answer would
    otherwise permit. Everything else in the folder belongs to the pipeline (the registry, the
    notes, the queue, the history) or to Claude Code's own setup (`.claude/`, `.mcp.json`), which
    a session that could write it could use to run commands in the next one (D304). A document
    that tries to steer the session into writing elsewhere is refused here."""
    from watchdog import access
    from watchdog.vault import SESSION_WRITABLE, session_may_write
    key = _EDIT_TOOLS.get(tool)
    raw = (tool_input or {}).get(key) if key else None
    if not key:
        return None
    if not isinstance(raw, str) or not raw:
        return f"{tool} needs a file path inside the investigation folder."
    rel = _vault_relative(vault, raw)
    if rel is None:
        return (f"Watchdog only lets a session change files inside this investigation's folder; "
                f"{raw} is outside it.")
    if not session_may_write(rel):
        return (f"A session may only change its own pages ({', '.join(SESSION_WRITABLE)}); "
                f"{rel or raw} belongs to Watchdog or to Claude Code's setup. Use Watchdog's tools "
                "to change the investigation's records.")
    root = Path(os.path.realpath(vault))
    if access.enforced() and not access.is_granted(root):
        return "Watchdog hasn't been allowed to work in this investigation's folder."
    return None


def _vault_only_edits(vault: Path):
    async def hook(input_data, tool_use_id, context):
        reason = edit_outside_vault(vault, input_data.get("tool_name", ""),
                                    input_data.get("tool_input") or {})
        if reason is None:
            return {}
        return {"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                       "permissionDecision": "deny",
                                       "permissionDecisionReason": reason}}
    return hook


def first_prompt(vault: Path, mode: str, text: str | None) -> str | None:
    """The session's opening prompt: for Ask, the query skill on the question (the bare question
    when the vault has no `/watchdog-query`, since an unknown slash command would lose it)."""
    text = " ".join((text or "").split())
    if mode == "ask":
        if not text:
            return None
        has_skill = (vault / ".claude" / "commands" / "watchdog-query.md").exists()
        return f"/watchdog-query {text}" if has_skill else text
    if mode == "context":
        _seed_context(vault)
        return "/watchdog-context"
    return f"/watchdog-research {text}".strip()


def _seed_context(vault: Path) -> None:
    """Write `context.md` from the template when missing, for a Background session."""
    from watchdog.projects import load as load_projects
    from watchdog.vault import render_template as _render_template
    path = vault / "context.md"
    if path.exists():
        return
    info = next((v for v in load_projects().values() if Path(v["path"]).resolve() == vault.resolve()), None)
    name = info["name"] if info else vault.name
    description = (info or {}).get("description") or (
        "<!-- One paragraph, written as open questions: what do you want to understand or explore? -->")
    path.write_text(_render_template("context.md", name=name, description=description), encoding="utf-8")


def _block_text(content) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    return "\n".join(part.get("text", "") for part in content if isinstance(part, dict))


def _record_session(vault: Path, kind: str = "session") -> None:
    """Around each turn, record the vault's history (D286): before it, anything changed since the
    last version; after it, what the session changed — its own pages, and, unless a run is
    writing the vault, anything else it changed through Watchdog's tools."""
    from watchdog.pipeline import history
    scope = ["queries/", "wiki/", "context.md"] if history.run_in_progress(vault) else None
    history.safe_snapshot(vault, {"kind": kind}, scope)


def _title(mode: str, text: str | None) -> str:
    text = " ".join((text or "").split())
    base = {"ask": "Ask", "context": "Seed context", "research": "Research"}[mode]
    if mode == "context" or not text:
        return base
    return (text if mode == "ask" else f"{base}: {text}")[:60]


def _friendly(error: Exception | str) -> str:
    text = str(error)
    low = text.lower()
    if type(error).__name__ == "CLINotFoundError":
        return _MISSING_HINT
    if any(w in low for w in ("not logged in", "/login", "authenticat", "invalid api key", "401")):
        return _AUTH_HINT
    return text or "The session ended unexpectedly."


class Session:
    def __init__(self, vault: Path, mode: str, model: str | None, title: str, sid: str | None = None):
        self.id = sid or uuid.uuid4().hex
        self.vault = vault
        self.mode = mode
        self.model = model
        self.title = title
        self.started = _now()
        self.updated = self.started
        self.messages: list[dict] = []
        self.sdk_session_id: str | None = None
        self.client = None
        self.lock: asyncio.Lock | None = None
        self.permissions: dict[str, asyncio.Future] = {}
        # "Allow for this conversation" answers (D308): the permission card's scope keys. Held here
        # only, never saved with the chat, so they end when the chat is closed or the app quits,
        # and a resumed chat asks again.
        self.allowed: set[str] = set()
        self.tools: dict[str, dict] = {}           # tool_use id -> its message
        self.stream_id: str | None = None
        self.cost = 0.0
        self.key_label: str | None = None    # the labelled Anthropic key the session bills (#690)
        self.auth_mode: str | None = None    # "api-key" or "subscription", from Claude Code's init message
        self.usage_totals: dict | None = None  # the SDK's running totals at the last result (D296)
        self.closed = False

    def record(self) -> dict:
        return {"session": self.id, "vault": str(self.vault), "mode": self.mode, "title": self.title,
                "started": self.started, "updated": self.updated, "model": self.model,
                "sdk_session_id": self.sdk_session_id, "cost_usd": self.cost, "key_label": self.key_label,
                "usage_totals": self.usage_totals, "messages": self.messages}


# The Watchdog tools, in plain words for the permission card (D304).
_WATCHDOG_TOOL_TITLES = {
    "timeline": "Rebuild the timeline",
    "write_entity": "Rewrite an entity's summary and timeline",
    "watchlist_add": "Add to the watch list",
    "contradiction_add": "Record a contradiction",
    "search": "Search the documents",
    "leads": "List leads",
    "check_citations": "Check citations",
    "research_seen": "List sources already captured",
    "wayback_archive": "Save a page to the Wayback Machine",
}

# What "Allow for this conversation" covers for a Watchdog tool that writes (D308): the whole tool,
# every entity included. `contradiction_add` has no entry: it is asked about every time.
_WATCHDOG_TOOL_SCOPES = {
    "write_entity": "rewriting any entity's summary and timeline",
    "watchlist_add": "adding terms to the watch list",
    "timeline": "rebuilding the timeline",
    "wayback_archive": "saving pages to the Wayback Machine",
}

_UNTIL = "until you close this chat or quit Watchdog"
_HISTORY = "Every change is recorded in version history, so you can see what was there before."


def _entity_name(vault: Path, entity_id) -> str:
    from watchdog.pipeline.json_io import _read_json_or
    entry = _read_json_or(Path(vault) / ".watchdog" / "registry" / "entities.json", {}).get(entity_id)
    name = entry.get("name") if isinstance(entry, dict) else None
    return name or str(entity_id)


def session_tools_web() -> tuple[str, ...]:
    from watchdog.session_tools import WEB_TOOLS
    return WEB_TOOLS


def permission_request(vault: Path, tool: str, tool_input: dict) -> dict:
    """What the permission card says about a tool call, and what an "Allow for this conversation"
    answer covers (D304, D308). For the Watchdog tools that write, it covers the whole tool (every
    entity) until the chat is closed; `contradiction_add` is never remembered, so each one is a
    deliberate yes. For a built-in tool it covers the tool within one scope: a folder of the vault
    for a file tool, a web site for a fetch, one command for Skill.

    Returns `{title, description, scope, scope_key, once_only, note}`: `scope` is the plain-words
    label for what a conversation-long answer covers (None for the whole tool, or when `once_only`),
    `note` the sentence the card shows under the buttons."""
    from urllib.parse import urlsplit
    inp = tool_input if isinstance(tool_input, dict) else {}
    title, description, scope, part = tool, None, None, None
    once_only, changes = False, False
    prefix = "mcp__watchdog__"
    if tool.startswith(prefix):
        name = tool[len(prefix):]
        title = _WATCHDOG_TOOL_TITLES.get(name, name.replace("_", " "))
        changes = name not in session_tools_web()
        scope = _WATCHDOG_TOOL_SCOPES.get(name)
        if name == "write_entity":
            entity = _entity_name(vault, inp.get("entity_id"))
            n = len(inp.get("timeline_events") or [])
            description = (f"Replace the summary of {entity} and its {n} timeline "
                           f"event{'s' if n != 1 else ''} with the text below.")
        elif name == "contradiction_add":
            once_only = True
            entity = _entity_name(vault, inp.get("entity_id"))
            description = (f"Add a contradiction to {entity}: “{inp.get('label', '')}”, "
                           f"{inp.get('a', '')} ({inp.get('a_doc', '')}) against "
                           f"{inp.get('b', '')} ({inp.get('b_doc', '')}).")
        elif name == "watchlist_add":
            terms = [str(t) for t in inp.get("terms") or []]
            description = "Add to the watch list: " + ", ".join(terms[:10]) + ("…" if len(terms) > 10 else "")
        elif name == "timeline":
            description = "Write the timeline page again from the stored events."
        elif name == "wayback_archive":
            description = (f"Ask the Internet Archive to save {inp.get('url', '')}. Watchdog sends it "
                           "with your Wayback Machine keys; Claude gets back only the archive link.")
    elif tool in _EDIT_TOOLS:
        raw = inp.get(_EDIT_TOOLS[tool])
        rel = _vault_relative(vault, raw) if isinstance(raw, str) and raw else None
        folder = (rel.split("/")[0] + "/") if rel and "/" in rel else (rel or "?")
        part = f"path:{folder}"
        scope = f"changes to files in {folder}" if folder.endswith("/") else f"changes to {folder}"
        description = f"Change {rel or raw}."
        changes = True
    elif tool == "WebFetch":
        host = urlsplit(str(inp.get("url") or "")).hostname or "?"
        part, scope = f"host:{host}", f"fetching pages on {host}"
        description = f"Fetch {inp.get('url')}"
    elif tool == "Skill":
        name = str(inp.get("skill") or inp.get("command") or "?")
        part, scope = f"skill:{name}", f"running /{name.lstrip('/')}"
        description = f"Run the /{name.lstrip('/')} command."
    elif tool == "WebSearch":
        description = f"Search the web for “{inp.get('query', '')}”."
    if once_only:
        scope = None
        note = ("Watchdog asks about each contradiction separately, so recording one is always your "
                "own decision.")
    else:
        what = scope or f"every use of {title}"
        note = f"“Allow for this conversation” covers {what}, {_UNTIL}."
    if changes:
        note += " " + _HISTORY
    return {"title": title, "description": description, "scope": scope, "once_only": once_only,
            "note": note, "scope_key": f"{tool}|{part}" if part else tool}


def _check_session_key(vault: Path) -> None:
    """Refuse to open a session whose investigation chose an Anthropic key this computer lacks,
    or in the sample investigation, which never reaches a real model (D307)."""
    from watchdog import auth
    from watchdog.ops import sample
    if sample.is_sample_folder(vault):
        raise RpcError(sample.CHAT_REFUSAL, code="sample")
    try:
        a = auth.resolve_auth("anthropic", vault, session=True)
    except auth.KeyChoiceError as e:
        raise RpcError(str(e), code="key_missing") from None
    _check_key_limit(a.get("key") if a.get("mode") == "api-key" else None)


def _check_key_limit(key: str | None) -> None:
    """Refuse a new conversation, or a new question in one, once the Anthropic key that would pay
    has reached its spending limit (D347)."""
    if not key:
        return
    from watchdog import key_spend
    msg = key_spend.check_key("anthropic", key)
    if msg:
        raise RpcError(msg, code="key_limit")


def _run_loop(loop: asyncio.AbstractEventLoop) -> None:
    """The chat thread's body. Every open session lives on this one loop, so nothing a session does
    may end it (D304): a `SystemExit` or `KeyboardInterrupt` that escapes a task (asyncio lets those
    two through `run_forever`) is reported and the loop goes on, keeping the other sessions alive.
    It returns only when the loop is stopped."""
    import sys
    import traceback
    while True:
        try:
            loop.run_forever()
            return
        except BaseException:  # noqa: BLE001 — the loop must outlive any one session's failure
            sys.stderr.write("A chat session raised an error the loop caught:\n" + traceback.format_exc())


class ChatManager:
    def __init__(self):
        self.sessions: dict[str, Session] = {}
        self._loop: asyncio.AbstractEventLoop | None = None
        self._lock = threading.Lock()

    # ── plumbing ────────────────────────────────────────────────────────────────────────
    def loop(self) -> asyncio.AbstractEventLoop:
        with self._lock:
            if self._loop is None:
                loop = asyncio.new_event_loop()
                threading.Thread(target=_run_loop, args=(loop,), name="chat-loop", daemon=True).start()
                self._loop = loop
            return self._loop

    def submit(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self.loop())

    def status(self, s: Session, state: str, detail: str | None = None, cost: float | None = None) -> None:
        rpc.emit("chat.status", {"session": s.id, "state": state, "detail": detail, "cost_usd": cost})

    def add_message(self, s: Session, role: str, text: str = "", tool: dict | None = None,
                    mid: str | None = None) -> dict:
        msg = {"id": mid or uuid.uuid4().hex, "role": role, "text": text, "ts": _now()}
        if tool is not None:
            msg["tool"] = tool
        s.messages.append(msg)
        s.updated = msg["ts"]
        rpc.emit("chat.message", {"session": s.id, "message": msg})
        return msg

    def save(self, s: Session) -> None:
        try:
            d = chats_dir()
            d.mkdir(parents=True, exist_ok=True)
            tmp = d / f".{s.id}.tmp"
            tmp.write_text(json.dumps(s.record(), ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, d / f"{s.id}.json")
        except OSError:
            pass

    # ── a turn ──────────────────────────────────────────────────────────────────────────
    async def _can_use_tool(self, s: Session, tool: str, tool_input: dict, ctx):
        from claude_agent_sdk import PermissionResultAllow, PermissionResultDeny
        ask = permission_request(s.vault, tool, tool_input)
        key = ask["scope_key"]
        if not ask["once_only"] and key in s.allowed:
            return PermissionResultAllow()
        request_id = uuid.uuid4().hex
        fut = asyncio.get_running_loop().create_future()
        s.permissions[request_id] = fut
        description = ask["description"] or getattr(ctx, "title", None) or f"Claude wants to use {tool}."
        rpc.emit("chat.permission", {"session": s.id, "request_id": request_id, "tool": tool,
                                     "input": tool_input, "description": description,
                                     "title": ask["title"], "scope": ask["scope"],
                                     "once_only": ask["once_only"], "note": ask["note"]})
        try:
            allow, for_conversation = await fut
        finally:
            s.permissions.pop(request_id, None)
        if allow:
            if for_conversation and not ask["once_only"]:
                s.allowed.add(key)
            return PermissionResultAllow()
        return PermissionResultDeny(message="The user declined this action.")

    async def _connect(self, s: Session) -> None:
        if s.client is not None:
            return

        async def can_use(tool, tool_input, ctx):
            return await self._can_use_tool(s, tool, tool_input, ctx)

        client = make_client(build_options(s, can_use))
        await client.connect()
        s.client = client

    async def _turn(self, s: Session, prompt: str) -> None:
        if s.lock is None:
            s.lock = asyncio.Lock()
        async with s.lock:
            if s.closed:
                return
            self.status(s, "thinking")
            await asyncio.to_thread(_record_session, s.vault, "found")
            try:
                await self._connect(s)
                await s.client.query(prompt)
                async for message in s.client.receive_response():
                    self._handle(s, message)
            except asyncio.CancelledError:
                raise
            except (Exception, SystemExit) as e:  # noqa: BLE001 — every failure reaches the app as a status
                self.status(s, "error", _friendly(e))
            finally:
                self.save(s)
                await asyncio.to_thread(_record_session, s.vault)

    def _handle(self, s: Session, message) -> None:
        from claude_agent_sdk import (AssistantMessage, ResultMessage, StreamEvent, SystemMessage,
                                      TextBlock, ToolResultBlock, ToolUseBlock, UserMessage)
        if isinstance(message, SystemMessage):
            if message.subtype == "init" and isinstance(message.data, dict):
                from watchdog.pipeline import session_usage
                s.auth_mode = session_usage.auth_mode_from_source(message.data.get("apiKeySource")) or s.auth_mode
        elif isinstance(message, StreamEvent):
            event = message.event or {}
            delta = event.get("delta") or {}
            if event.get("type") == "content_block_delta" and delta.get("type") == "text_delta":
                if s.stream_id is None:
                    s.stream_id = uuid.uuid4().hex
                rpc.emit("chat.delta", {"session": s.id, "message_id": s.stream_id,
                                        "text": delta.get("text", "")})
        elif isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, TextBlock) and block.text.strip():
                    self.add_message(s, "assistant", block.text, mid=s.stream_id)
                    s.stream_id = None
                elif isinstance(block, ToolUseBlock):
                    msg = self.add_message(s, "tool", "", tool={"name": block.name, "input": block.input,
                                                                "result": None, "is_error": False},
                                           mid=block.id)
                    s.tools[block.id] = msg
            s.stream_id = None
        elif isinstance(message, UserMessage):
            blocks = message.content if isinstance(message.content, list) else []
            for block in blocks:
                if isinstance(block, ToolResultBlock) and block.tool_use_id in s.tools:
                    msg = s.tools[block.tool_use_id]
                    msg["tool"]["result"] = _block_text(block.content)
                    msg["tool"]["is_error"] = bool(block.is_error)
                    rpc.emit("chat.message", {"session": s.id, "message": msg})
        elif isinstance(message, ResultMessage):
            if message.session_id:
                s.sdk_session_id = message.session_id
            self._record_usage(s, message)
            if message.is_error:
                detail = _friendly(message.result or "; ".join(message.errors or []) or "")
                self.status(s, "error", detail, s.cost)
            else:
                self.status(s, "idle", None, s.cost)

    def _record_usage(self, s: Session, message) -> None:
        """Add this turn's share of the SDK's running totals to the chat's cost and record it in
        the vault's usage files and telemetry as an `ask` or `research` call (D296). The SDK's
        totals are cumulative across turns, so only the difference is this turn's."""
        from watchdog.pipeline import session_usage
        share, s.usage_totals = session_usage.turn(s.usage_totals, message)
        s.cost += share["cost_usd"]
        if not share["models"] and not share["cost_usd"] and not message.usage:
            return
        auth_mode = s.auth_mode or ("api-key" if s.key_label else "subscription")
        try:
            call = session_usage.call_record(share, message, task=session_usage.task_for(s.mode),
                                             session_id=s.id, model=s.model, auth_mode=auth_mode,
                                             key_label=s.key_label)
            session_usage.record(s.vault, session_id=s.id, mode=s.mode, title=s.title, call=call)
        except Exception:  # noqa: BLE001 — recording usage never breaks a conversation
            pass

    # ── RPC operations ──────────────────────────────────────────────────────────────────
    def start(self, vault: Path, mode: str, model: str | None, prompt: str | None) -> dict:
        if mode not in MODES:
            raise RpcError(f"mode must be one of {', '.join(MODES)}.", code="bad_params")
        model = model or DEFAULT_CHAT_MODEL
        if model not in CHAT_MODELS:
            raise RpcError(f"model must be one of {', '.join(CHAT_MODELS)}.", code="bad_params")
        _check_session_key(vault)
        opening = first_prompt(vault, mode, prompt)
        s = Session(vault, mode, model, _title(mode, prompt))
        self.sessions[s.id] = s
        self.save(s)
        if opening:
            self.add_message(s, "user", opening)
            self.submit(self._turn(s, opening))
        else:
            self.status(s, "idle")
        return {"session": s.id}

    def _get(self, sid: str) -> Session:
        s = self.sessions.get(sid)
        if s is None:
            raise RpcError("That chat is not open.", code="not_found")
        return s

    def send(self, sid: str, text: str) -> None:
        s = self._get(sid)
        if not (text or "").strip():
            raise RpcError("Nothing to send.", code="bad_params")
        if s.key_label:
            from watchdog import key_spend
            key = key_spend.ref_for_label("anthropic", s.key_label)
            msg = key_spend.stop_message("anthropic", s.key_label, key) if key else None
            if msg:
                raise RpcError(msg, code="key_limit")
        self.add_message(s, "user", text)
        self.submit(self._turn(s, text))

    def interrupt(self, sid: str) -> None:
        s = self._get(sid)
        if s.client is None:
            return

        async def go():
            try:
                await s.client.interrupt()
            except Exception:  # noqa: BLE001
                pass
        self.submit(go())

    def answer(self, sid: str, request_id: str, allow: bool, for_conversation: bool) -> None:
        s = self._get(sid)
        fut = s.permissions.get(request_id)
        if fut is None:
            raise RpcError("That permission request is no longer waiting.", code="not_found")
        self.loop().call_soon_threadsafe(lambda: fut.done() or fut.set_result((bool(allow), bool(for_conversation))))

    def close(self, sid: str) -> dict:
        s = self._get(sid)
        s.closed = True

        async def go():
            for fut in list(s.permissions.values()):
                if not fut.done():
                    fut.set_result((False, False))
            if s.client is not None:
                try:
                    await s.client.disconnect()
                except Exception:  # noqa: BLE001
                    pass
                s.client = None
        try:
            self.submit(go()).result(timeout=15)
        except Exception:  # noqa: BLE001
            pass
        self.save(s)
        self.status(s, "closed", None, s.cost)
        self.sessions.pop(sid, None)
        from watchdog.pipeline import research
        return {"ok": True, "research_queued": research.pending_count(s.vault)}

    def resume(self, sid: str) -> dict:
        if sid in self.sessions:
            return {"session": sid}
        data = self.load(sid)
        # The same folder checks as `start`: the saved folder may have moved, stopped being an
        # investigation or lost its folder access since the chat was saved.
        from watchdog.gui.vaultio import require_vault
        vault = require_vault(data.get("vault"))
        s = Session(vault, data["mode"], data.get("model"), data.get("title") or "Chat", sid)
        s.started, s.updated = data.get("started", s.started), data.get("updated", s.updated)
        s.messages = data.get("messages") or []
        s.sdk_session_id = data.get("sdk_session_id")
        s.cost = data.get("cost_usd") or 0.0
        s.key_label = data.get("key_label")
        s.usage_totals = data.get("usage_totals")
        if not s.sdk_session_id:
            raise RpcError("That chat never started, so it cannot be resumed.", code="bad_params")
        _check_session_key(s.vault)
        self.sessions[sid] = s
        self.status(s, "idle", None, s.cost)
        return {"session": sid}

    def load(self, sid: str) -> dict:
        if not sid or not all(c.isalnum() for c in sid):
            raise RpcError("No such chat.", code="not_found")
        path = chats_dir() / f"{sid}.json"
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            raise RpcError("No such chat.", code="not_found") from e

    def list(self, vault: Path) -> list[dict]:
        out = []
        for path in chats_dir().glob("*.json") if chats_dir().is_dir() else []:
            try:
                d = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if Path(d.get("vault", "")).resolve() != vault.resolve():
                continue
            live = self.sessions.get(d["session"])
            if live:
                d = live.record()
            out.append({k: d.get(k) for k in ("session", "mode", "title", "started", "updated", "model")})
        return sorted(out, key=lambda r: r["updated"] or "", reverse=True)

    def get(self, sid: str) -> dict:
        s = self.sessions.get(sid)
        d = s.record() if s else self.load(sid)
        return {"session": sid, "mode": d["mode"], "title": d["title"], "messages": d["messages"]}

    def delete(self, sid: str) -> None:
        if sid in self.sessions:
            try:
                self.close(sid)
            except RpcError:
                pass
        try:
            self.load(sid)
            (chats_dir() / f"{sid}.json").unlink()
        except (RpcError, OSError):
            pass

    def shutdown(self) -> None:
        for sid in list(self.sessions):
            try:
                s = self.sessions[sid]
                s.closed = True
                self.save(s)
                if s.client is not None and self._loop is not None:
                    self.submit(s.client.disconnect()).result(timeout=5)
            except Exception:  # noqa: BLE001
                pass


MANAGER = ChatManager()
