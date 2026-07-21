"""Golden-vault parity test for a real reconcile MERGE — the safety net for #403 phase 3.

`test_golden_vault.py` pins a fixture whose reconcile pass is a no-op (its one recurring entity
has no duplicate). Phase 3 moves reconcile *before* the commit pass and turns a confirmed merge
into a staged-id rewrite instead of post-commit note surgery — a change that fixture is blind to.

This test closes the gap. Two documents are staged (via the real `postflight.run`, so the
artifacts are exactly what extraction would leave on disk) whose entities form a token-variant
candidate pair — "Laurentian University" ⊂ "Laurentian University of Sudbury" — which the
exact-name fold (phase 2) leaves separate and `reconcile.candidate_pairs` surfaces as one pair.
`orchestrate.finalize` then commits and reconciles, with the reconcile model mocked to CONFIRM
the merge. The full observable vault write-surface is hashed into a manifest, exactly as in
`test_golden_vault.py` (same normalizers, same exemptions).

Extraction is bypassed deliberately: it is not what phase 3 changes, and its call order is
non-deterministic under the mock, so staging the artifacts directly is both more targeted and
the only way to assign each entity to a fixed sha. `finalize` is the exact surface phase 3
touches (commit + reconcile).

The manifest's contract is that the reconcile-before-commit refactor leaves the committed vault
byte-identical: the merged entity's note, the merged-away redirect stub, the registry, and the
timeline must match what the old post-commit `merge_entities.run` produced. It was generated on
pre-phase-3 code and regenerated once in phase 4, when `_stage` started writing the companion
`result_<sha>.json` that a real extraction always leaves (see its docstring) — that is the batch
scope the phase-4 synthesis pass reads, so without it the fixture no longer exercised synthesis
at all. The regeneration touched only two hashes (`briefings/`, `log.md`): the document count
those files report went from 0 to 2, which is what a real two-document ingest records. The merge
write-surface above was unchanged. Regenerate deliberately, never reflexively:

    REGENERATE_GOLDEN=1 PYTHONPATH=src ~/.local/pipx/venvs/watchdog-intel/bin/pytest \
        tests/test_golden_merge.py
"""

import asyncio
import json
import os
from pathlib import Path

from watchdog import model_client
from watchdog.pipeline import orchestrate, postflight

from tests.test_golden_vault import _manifest
from tests.test_orchestrate import _queue_doc
from tests.test_write_vault import make_vault

GOLDEN = Path(__file__).parent / "golden" / "merge-manifest.json"

# The two documents' entities: same canonical type, token-variant names, so the exact-name fold
# (phase 2) leaves them separate and reconcile.candidate_pairs surfaces them as one pair. Ids sort
# with the shorter name first, so it is pair member "a"; the merge keeps it.
_KEEP_ID, _KEEP_NAME = "laurentian-university", "Laurentian University"
_MERGE_ID, _MERGE_NAME = "laurentian-university-of-sudbury", "Laurentian University of Sudbury"


def _raw_extraction(sha: str, filename: str, entity_id: str, entity_name: str) -> dict:
    """A pre-postflight extraction: one entity, one key_fact tagged to it (so post-flight explodes
    an evidence fragment onto the entity and the committed note gets an Analysis ledger)."""
    return {
        "document": {
            "sha256": sha, "filename": filename, "original_path": f"_INCOMING/{filename}",
            "title": f"{entity_name} filing", "document_type": "Filing",
            "date_of_document": "2024-03-01", "page_count": 1, "source": None, "obtained": None,
            "near_duplicate_of": None, "summary": f"A filing about {entity_name}.",
            "key_facts": [{"fact": f"{entity_name} reported a deficit in 2024.", "page": 1,
                           "basis": "stated", "entities": [entity_id]}],
        },
        "entities": [{
            "id": entity_id, "name": entity_name, "type": "Organization", "aliases": [],
            "summary": f"{entity_name} is a public university.",
            "timeline_events": [], "roles": [],
        }],
        "morgue_entity_id": entity_id, "morgue_document_type": "filing",
        "scratchpad": f"# notes\n- {entity_name}",
    }


def _stage(vault: Path, tmp_path: Path, sha: str, filename: str, entity_id: str, name: str,
           text: str) -> None:
    """Queue the document (for the morgue markdown the commit writes) and stage its extraction
    through the real post-flight, so `.watchdog/extracted/<sha>.json` is genuine post-flight
    output — validated, sanitized, key_facts exploded into evidence fragments.

    Also writes the companion `result_<sha>.json` that `_finish_extraction` leaves alongside every
    real extraction: it is the batch manifest `finalize` reads (`_load_results`) to know which shas
    this run committed — the scope for both the synthesis pass (#403 phase 4) and the briefing's
    document count. Bypassing full extraction (below) must not also skip this deterministic
    byproduct, or the fixture under-represents a real ingest."""
    _queue_doc(vault, sha=sha, filename=filename, text=text)
    raw = tmp_path / f"raw_{sha}.json"
    raw.write_text(json.dumps(_raw_extraction(sha, filename, entity_id, name)), encoding="utf-8")
    postflight.run(vault, raw)

    staged = json.loads((vault / ".watchdog" / "extracted" / f"{sha}.json").read_text(encoding="utf-8"))
    result = orchestrate._compact_result(sha, filename, staged, {}, 0.0, {})
    tmp = vault / ".watchdog" / "tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    (tmp / f"result_{sha}.json").write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")


def _mock_reconcile_merge(monkeypatch):
    """Post-ingest model mock: reconcile confirms the one candidate pair as a merge keeping the
    shorter-named entity; every other post-ingest call is an empty/canned no-op."""
    async def fake(*, task, prompt, schema, model=None, backend=None, max_retries=1, effort=None):
        parsed = {
            "reconcile": {"merges": [{"pair": 0, "keep_id": _KEEP_ID,
                                      "reason": "same university, name variant"}],
                          "contradictions": []},
            "entity-synthesis": {"entity_syntheses": []},
            "timeline-dedup": {"groups": []},
            "briefing": {"investigation_status": "Early days.",
                         "what_was_ingested": ["alpha.pdf — Filing", "beta.pdf — Filing"],
                         "new_entities": [_KEEP_NAME]},
        }.get(task, {})
        return model_client.ModelResult(parsed=parsed, text="", model="m",
                                        backend="claude-agent-sdk", auth_mode="subscription",
                                        cost_usd=0.01)

    monkeypatch.setattr(orchestrate.model_client, "acomplete_json", fake)


def _run_merge_fixture(tmp_path, monkeypatch) -> Path:
    vault = make_vault(tmp_path)
    _stage(vault, tmp_path, "a" * 64, "alpha.pdf", _KEEP_ID, _KEEP_NAME,
           "Laurentian University received a grant of $2,000,000 in 2024.")
    _stage(vault, tmp_path, "b" * 64, "beta.pdf", _MERGE_ID, _MERGE_NAME,
           "Laurentian University of Sudbury disclosed a deficit on March 1, 2024.")
    _mock_reconcile_merge(monkeypatch)
    asyncio.run(orchestrate.finalize(vault, post_model="haiku"))
    return vault


def test_merge_output_matches_golden(tmp_path, monkeypatch):
    vault = _run_merge_fixture(tmp_path, monkeypatch)

    # Guard the fixture itself: the merge must actually have happened, or the manifest would pin
    # a no-op and silently stop testing anything.
    entities = json.loads(
        (vault / ".watchdog" / "registry" / "entities.json").read_text(encoding="utf-8"))
    assert _KEEP_ID in entities, "keep entity missing — fixture did not run as expected"
    assert _MERGE_ID not in entities, "merge entity still present — reconcile merge did not apply"

    manifest = _manifest(vault)

    if os.environ.get("REGENERATE_GOLDEN"):
        GOLDEN.parent.mkdir(parents=True, exist_ok=True)
        GOLDEN.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return

    expected = json.loads(GOLDEN.read_text(encoding="utf-8"))
    missing = sorted(set(expected) - set(manifest))
    added = sorted(set(manifest) - set(expected))
    changed = sorted(p for p in set(expected) & set(manifest)
                     if expected[p] != manifest[p] and expected[p] != "<exempt>")

    assert not missing, f"vault files no longer written: {missing}"
    assert not added, f"vault files newly written: {added}"
    assert not changed, f"vault file content changed: {changed}"
