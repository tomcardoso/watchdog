"""Verify a model download against the operating system's trust store (D122, D355).

A network that inspects TLS re-signs traffic with its own root certificate, which IT installs in the
OS keychain but which certifi's bundled list (what `requests`, `httpx` and the Hugging Face hub use
by default) has never heard of. Every download then fails with `CERTIFICATE_VERIFY_FAILED`, though
the browser works. `truststore.inject_into_ssl` makes the global `ssl` module use the OS store; it is
patched for the length of one download only, and counted so that downloads running in several
threads do not unpatch each other.
"""

from __future__ import annotations

import contextlib
import threading

_lock = threading.Lock()
_depth = 0


@contextlib.contextmanager
def os_trust_store():
    global _depth
    import truststore
    with _lock:
        if _depth == 0:
            truststore.inject_into_ssl()
        _depth += 1
    try:
        yield
    finally:
        with _lock:
            _depth -= 1
            if _depth == 0:
                truststore.extract_from_ssl()
