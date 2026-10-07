"""chat.*: Claude Code sessions in the vault (see `watchdog.gui.chat`)."""

from __future__ import annotations

from watchdog.gui import chat
from watchdog.gui.rpc import SHUTDOWN_HOOKS, method
from watchdog.gui.vaultio import require_vault


@method("chat.start")
def start(vault, mode, model=None, prompt=None) -> dict:
    return chat.MANAGER.start(require_vault(vault), mode, model, prompt)


@method("chat.send")
def send(session, text) -> dict:
    chat.MANAGER.send(session, text)
    return {"ok": True}


@method("chat.interrupt")
def interrupt(session) -> dict:
    chat.MANAGER.interrupt(session)
    return {"ok": True}


@method("chat.close")
def close(session) -> dict:
    return chat.MANAGER.close(session)


@method("chat.permission")
def permission(session, request_id, allow, always=False) -> dict:
    chat.MANAGER.answer(session, request_id, allow, always)
    return {"ok": True}


@method("chat.list")
def list_chats(vault) -> list:
    return chat.MANAGER.list(require_vault(vault))


@method("chat.get")
def get(session) -> dict:
    return chat.MANAGER.get(session)


@method("chat.resume")
def resume(session) -> dict:
    return chat.MANAGER.resume(session)


@method("chat.delete")
def delete(session) -> dict:
    chat.MANAGER.delete(session)
    return {"ok": True}


SHUTDOWN_HOOKS.append(chat.MANAGER.shutdown)
