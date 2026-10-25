"""Bring this investigation up to date (D336): the pending migrations of `pipeline.migrations`,
run in order with the spending limit checked before and between model calls."""

from __future__ import annotations

import sys
from pathlib import Path

from watchdog.ops import op, require_vault


@op("migrate", engine="index", secrets=True,
    exit_code=lambda r: 1 if (r or {}).get("error") else 2 if (r or {}).get("spending_limit") else 0)
def migrate(rep, vault: Path) -> dict:
    """Run every migration this investigation has not had; a stop leaves the rest pending."""
    from watchdog.pipeline import migrations
    require_vault(vault)
    try:
        out = migrations.run(vault, say=rep.log, warn=rep.warn)
    except migrations.Busy as e:
        sys.exit(f"Error: {e}")
    rep.log(migrations.summary(out))
    return out
