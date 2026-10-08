"""Claude Code sessions inside the app, run through the Claude Agent SDK in the vault's folder.

They replace the terminal hand-off of `watchdog ask`, `ask --context` and `research`: the session
starts in the vault with `setting_sources=["project"]`, so the vault's own `.claude/` settings,
`CLAUDE.md` and `/watchdog-*` commands apply exactly as they do in a terminal, and the first
prompt is built the way the CLI builds it.

Every session runs on one asyncio event loop in a daemon thread; RPC handlers (which run on a
thread pool) submit coroutines with `run_coroutine_threadsafe`. Tool permissions are asked of the
app: `can_use_tool` emits `chat.permission` and waits on a future the app's `chat.permission`
answer resolves. Transcripts are saved as JSON under `~/.watchdog/gui/chats/<session>.json` with the
SDK's own session id, so a saved chat can be resumed.
"""

from __future__ import annotations

import asyncio
import datetime
import fnmatch
import json
import os
import re
import sys
import threading
import uuid
from pathlib import Path

from watchdog.gui import rpc
from watchdog.gui.rpc import RpcError

MODES = ("ask", "context", "research")
_AUTH_HINT = ("Claude is not signed in. Sign in under Settings → Models & keys, then try again.")
_MISSING_HINT = ("Claude Code, which comes with Watchdog's engine, could not be started. Repair the "
                 "engine under Settings → Setup, then try again.")


def _now() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


def chats_dir() -> Path:
    from watchdog.cmd import base
    return base.WATCHDOG_HOME / "gui" / "chats"


def make_client(options):
    """The SDK client for a session. Replaced in tests."""
    from claude_agent_sdk import ClaudeSDKClient
    return ClaudeSDKClient(options)


def build_options(session: "Session", can_use_tool):
    from claude_agent_sdk import ClaudeAgentOptions
    from watchdog.model_catalog import resolve_model_id
    kwargs = dict(cwd=str(session.vault), setting_sources=["project"], include_partial_messages=True,
                  system_prompt={"type": "preset", "preset": "claude_code"}, can_use_tool=can_use_tool)
    # The session's `watchdog …` commands (the vault's slash commands call them) must resolve to
    # the engine this server runs in, and Claude Code must be the copy bundled with the SDK.
    kwargs["env"] = {"PATH": os.pathsep.join([str(Path(sys.executable).parent), os.environ.get("PATH", "")])}
    from watchdog.gui.engine_setup import bundled_claude_path
    if bundled_claude_path():
        kwargs["cli_path"] = bundled_claude_path()
    if session.model:
        kwargs["model"] = resolve_model_id(session.model)
    if session.sdk_session_id:
        kwargs["resume"] = session.sdk_session_id
    from claude_agent_sdk import HookMatcher
    kwargs["hooks"] = {"PreToolUse": [
        HookMatcher(matcher="|".join(_EDIT_TOOLS), hooks=[_vault_only_edits(session.vault)]),
        HookMatcher(matcher="Bash", hooks=[_confined_shell(session.vault)]),
    ]}
    if sandbox_available():
        kwargs["sandbox"] = sandbox_settings()
    return ClaudeAgentOptions(**kwargs)


# ── shell commands (D274) ───────────────────────────────────────────────────────────────
# Claude's file tools are confined by the hook above, but a shell command could write anywhere
# the user's account can. On macOS every Bash command runs inside Claude Code's sandbox (Seatbelt,
# built into the system), which lets it write only to the investigation folder, the temp folder
# and Watchdog's settings folder, with no unsandboxed retry; Claude Code refuses to start rather
# than run without it. Elsewhere the sandbox isn't dependable (it is unavailable on Windows and
# needs bubblewrap and socat on Linux), so a session's shell is limited to the `watchdog` commands
# the vault pre-approves, each run as a single plain command.

def sandbox_available() -> bool:
    return sys.platform == "darwin"


def sandbox_settings() -> dict:
    home = Path.home() / ".watchdog"
    return {
        "enabled": True,
        "failIfUnavailable": True,
        "allowUnsandboxedCommands": False,
        # Commands the vault doesn't pre-approve still ask the user first.
        "autoAllowBashIfSandboxed": False,
        "filesystem": {
            # `watchdog` commands update the project registry and usage log here.
            "allowWrite": [str(home)],
            # The folder-access list and the provider keys are never a command's to change.
            "denyWrite": [str(home / "access.json"), str(home / "credentials.json")],
        },
    }


# Anything that chains, substitutes or redirects: a pre-approved prefix must not carry a second
# command along with it.
_SHELL_META = re.compile(r"[;&|`$<>(){}\\\n\r]")


def _allowed_shell_patterns() -> list[str]:
    from watchdog.cmd.base import _VAULT_PERMISSIONS
    return [rule[len("Bash("):-1] for rule in _VAULT_PERMISSIONS if rule.startswith("Bash(")]


def shell_refusal(command: str) -> str | None:
    """Why a session may not run `command` where there is no sandbox, or None."""
    if sandbox_available():
        return None
    cmd = (command or "").strip()
    if cmd and not _SHELL_META.search(cmd):
        for pattern in _allowed_shell_patterns():
            if fnmatch.fnmatchcase(cmd, pattern):
                return None
    return ("On this computer Watchdog lets a session run only its own pre-approved watchdog "
            "commands, one at a time. Use the file and search tools instead.")


def _confined_shell(vault: Path):
    async def hook(input_data, tool_use_id, context):
        reason = shell_refusal((input_data.get("tool_input") or {}).get("command", ""))
        if reason is None:
            return {}
        return {"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                       "permissionDecision": "deny",
                                       "permissionDecisionReason": reason}}
    return hook


# Claude Code's file-editing tools and the input key naming the file each one changes.
_EDIT_TOOLS = {"Write": "file_path", "Edit": "file_path", "MultiEdit": "file_path",
               "NotebookEdit": "notebook_path"}


def edit_outside_vault(vault: Path, tool: str, tool_input: dict) -> str | None:
    """Why `tool` may not run, or None. A session edits files only inside its own investigation
    folder, and only when the user has allowed Watchdog there (watchdog/access.py) — whatever the
    vault's settings or an "always allow" answer would otherwise permit. A document that tries to
    steer the session into writing elsewhere is refused here."""
    from watchdog import access
    key = _EDIT_TOOLS.get(tool)
    raw = (tool_input or {}).get(key) if key else None
    if not key:
        return None
    if not isinstance(raw, str) or not raw:
        return f"{tool} needs a file path inside the investigation folder."
    target = Path(raw) if Path(raw).is_absolute() else Path(vault) / raw
    real_target = Path(os.path.realpath(target))
    root = Path(os.path.realpath(vault))
    if real_target != root and root not in real_target.parents:
        return (f"Watchdog only lets a session change files inside this investigation's folder; "
                f"{raw} is outside it.")
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
    """The session's opening prompt, exactly as the CLI's command would pass it to `claude`."""
    text = (text or "").strip()
    if mode == "ask":
        from watchdog.cmd.ask import prompt_for
        return prompt_for(text, (vault / ".claude" / "commands" / "watchdog-query.md").exists())
    if mode == "context":
        _seed_context(vault)
        return "/watchdog-context"
    return f"/watchdog-research {text}".strip()


def _seed_context(vault: Path) -> None:
    """Write `context.md` from the template when missing, as `watchdog ask --context` does."""
    from watchdog.cmd.base import _render_template, load_projects
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
        self.always: set[str] = set()
        self.tools: dict[str, dict] = {}           # tool_use id -> its message
        self.stream_id: str | None = None
        self.cost = 0.0
        self.closed = False

    def record(self) -> dict:
        return {"session": self.id, "vault": str(self.vault), "mode": self.mode, "title": self.title,
                "started": self.started, "updated": self.updated, "model": self.model,
                "sdk_session_id": self.sdk_session_id, "cost_usd": self.cost, "messages": self.messages}


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
                threading.Thread(target=loop.run_forever, name="chat-loop", daemon=True).start()
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
        # "Always" for a shell command means that exact command, never every shell command.
        key = f"Bash:{(tool_input or {}).get('command', '')}" if tool == "Bash" else tool
        if key in s.always:
            return PermissionResultAllow()
        request_id = uuid.uuid4().hex
        fut = asyncio.get_running_loop().create_future()
        s.permissions[request_id] = fut
        description = getattr(ctx, "title", None) or f"Claude wants to use {tool}."
        rpc.emit("chat.permission", {"session": s.id, "request_id": request_id, "tool": tool,
                                     "input": tool_input, "description": description})
        try:
            allow, always = await fut
        finally:
            s.permissions.pop(request_id, None)
        if allow:
            if always:
                s.always.add(key)
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
            try:
                await self._connect(s)
                await s.client.query(prompt)
                async for message in s.client.receive_response():
                    self._handle(s, message)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 — every failure must reach the app as a status
                self.status(s, "error", _friendly(e))
            finally:
                self.save(s)

    def _handle(self, s: Session, message) -> None:
        from claude_agent_sdk import (AssistantMessage, ResultMessage, StreamEvent, TextBlock,
                                      ToolResultBlock, ToolUseBlock, UserMessage)
        if isinstance(message, StreamEvent):
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
            s.cost += message.total_cost_usd or 0.0
            if message.is_error:
                detail = _friendly(message.result or "; ".join(message.errors or []) or "")
                self.status(s, "error", detail, s.cost)
            else:
                self.status(s, "idle", None, s.cost)

    # ── RPC operations ──────────────────────────────────────────────────────────────────
    def start(self, vault: Path, mode: str, model: str | None, prompt: str | None) -> dict:
        if mode not in MODES:
            raise RpcError(f"mode must be one of {', '.join(MODES)}.", code="bad_params")
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

    def answer(self, sid: str, request_id: str, allow: bool, always: bool) -> None:
        s = self._get(sid)
        fut = s.permissions.get(request_id)
        if fut is None:
            raise RpcError("That permission request is no longer waiting.", code="not_found")
        self.loop().call_soon_threadsafe(lambda: fut.done() or fut.set_result((bool(allow), bool(always))))

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
        s = Session(Path(data["vault"]), data["mode"], data.get("model"), data.get("title") or "Chat", sid)
        s.started, s.updated = data.get("started", s.started), data.get("updated", s.updated)
        s.messages = data.get("messages") or []
        s.sdk_session_id = data.get("sdk_session_id")
        s.cost = data.get("cost_usd") or 0.0
        if not s.sdk_session_id:
            raise RpcError("That chat never started, so it cannot be resumed.", code="bad_params")
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
