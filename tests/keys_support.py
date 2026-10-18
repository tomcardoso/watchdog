"""Credentials in the format Watchdog stores them, for tests."""


def item(key, *, kid: str = "default", label: str = "Default") -> dict:
    return {"id": kid, "label": label, "key": key}


def stored(**keys) -> dict:
    """`{"keys": …}` contents with one key, named Default, per provider. A value may be the key
    or the app's encrypted blob."""
    return {p: {"default": "default", "items": [item(k)]} for p, k in keys.items()}
