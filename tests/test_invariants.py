"""Named guard tests for ARCHITECTURE.md §15's invariants (I1-I5), #349.

Each test name starts with the invariant id it guards (`test_I1_...`, `test_I2_...`, etc.), so
a failing rule shows up as a specific, named red bar instead of relying on code review to catch
a regression. This file exists to make the rule -> test mapping explicit; it does not replace
the broader unit coverage those modules already have elsewhere (some overlap with existing
tests is expected and fine).

Deliberately NOT guarded here, and why:

- **I1's `document.summary` grounding.** ARCHITECTURE §15 is explicit that this is "a prompt
  instruction, not a verified postcondition" — no code checks the digest's claims against
  `key_facts`, so there is nothing mechanical to assert. A hallucinated summary claim would not
  be caught by any test, guard or otherwise.
- **I2's "chew costs no API tokens" as a cost claim.** The import-graph and monkeypatched-socket
  guards below verify no network *library* is reachable/usable from the chew path; they cannot
  prove the absence of a cost line item, which is a billing fact, not a code property.
- **I3's "skills are read directly from the package, never duplicated on disk by a background
  process."** Covered here only at the two boundaries that matter mechanically (classifier index
  contents, and vault creation not copying `skills/records/`). We do not re-guard the merge
  semantics between package and user skill directories — `tests/test_skills_catalog.py` already
  covers that in detail and this file does not duplicate it.
- **I4's provider-side interpretation of the `effort` knob** (e.g. that Haiku silently drops it).
  That is provider behaviour, already covered in `tests/test_model_client.py`; the guard below
  only asserts the *pipeline* never changes what it asks for on retry.
- **I5's Claude Code research session itself** (the `/watchdog-research` skill that curates
  URLs) is a separate, uncapturable process — nothing in this repo can mechanically assert what
  an interactive session does. The guard below instead asserts the deterministic Python side of
  the boundary: `pipeline/research.py`'s enumerable write paths never touch vault notes.

I2's runtime guard was confirmed to run hermetically (the direct-text preprocessing path does
not import Docling), so both the static and runtime layers described in the issue are present.
"""

import asyncio
import ast
import importlib.resources
import socket
from pathlib import Path

from watchdog import model_client as mc
from watchdog import skills_catalog as sc
from watchdog.pipeline import orchestrate, preprocess, research

_SRC = Path(__file__).resolve().parents[1] / "src" / "watchdog"


# ── I1 — deterministic code writes; the model only reasons ───────────────────

def test_I1_stamp_document_overrides_every_model_lied_field():
    """`_stamp_document` is the single place identity/provenance/derived fields are set. Build a
    fake extraction where the model lies about every field it stamps, and confirm the pipeline's
    own values win on all of them — sha256, filename, original_path, page_count, record_skill,
    record_skill_hash, extract_model, extract_effort, source/obtained (from the sidecar), and
    the derived morgue_document_type/morgue_entity_id."""
    pf = {"filename": "real.pdf", "original_path": "_INCOMING/real.pdf",
          "page_count": 7, "pages": [{}],
          "sidecar": "source: FOI A-2026-001\nobtained: 2026-01-02\n"}

    lied_extraction = {
        "document": {
            "sha256": "0" * 64,                       # model's fabricated hash
            "filename": "totally-different.pdf",
            "original_path": "_INCOMING/elsewhere.pdf",
            "page_count": 999,
            "record_skill": "wrong-skill.md",
            "record_skill_hash": "deadbeefdead",
            "extract_model": "claude-opus-9000",
            "extract_effort": "ultra",
            "source": "the model made this up",
            "obtained": "1999-01-01",
            "document_type": "Some Type",
        },
        "morgue_document_type": "some-other-slug",
        "morgue_entity_id": "Acme Corp/subsidiary",   # would break the morgue path if kept raw
    }

    orchestrate._stamp_document(
        lied_extraction, sha="realsha256", pf=pf, skill_label="court-documents.md",
        skill_text="SKILL BODY", extract_model="sonnet", extract_effort="low",
    )

    d = lied_extraction["document"]
    assert d["sha256"] == "realsha256"
    assert d["filename"] == "real.pdf"
    assert d["original_path"] == "_INCOMING/real.pdf"
    assert d["page_count"] == 7
    assert d["record_skill"] == "court-documents.md"
    assert d["record_skill_hash"] != "deadbeefdead"
    assert d["extract_model"] == mc.resolve_model_id("sonnet")
    assert d["extract_effort"] == "low"
    assert d["source"] == "FOI A-2026-001"           # sidecar wins, not the model's claim
    assert d["obtained"] == "2026-01-02"
    assert lied_extraction["morgue_document_type"] == "some-type"     # derived, not "some-other-slug"
    assert lied_extraction["morgue_entity_id"] == "acme-corp-subsidiary" or \
        "/" not in lied_extraction["morgue_entity_id"]


# ── I2 — local-first preprocessing, no source-doc egress ──────────────────────

_NETWORK_LIBS = {"httpx", "requests", "aiohttp", "anthropic", "openai", "urllib.request"}


def _module_level_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in tree.body:            # module level only — lazy imports inside functions are fine
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_I2_chew_path_imports_no_network_library_at_module_level():
    """Static guard: none of the chew-path modules (preprocess.py, preprocess_batch.py, and the
    watchdog.* modules they import) may import a network library at module level. Docling/OCR
    imports are lazy (inside functions) specifically so the module can be imported, and its
    direct-text path exercised, without ever touching the network — this guard would catch a
    regression that hoisted one of those imports to module scope, or added a new one."""
    chew_files = [
        _SRC / "pipeline" / "preprocess.py",
        _SRC / "pipeline" / "preprocess_batch.py",
        _SRC / "cmd" / "live.py",   # imported by preprocess_batch
    ]
    for f in chew_files:
        imports = _module_level_imports(f)
        offending = {
            lib for lib in _NETWORK_LIBS
            if any(mod == lib or mod.startswith(lib + ".") for mod in imports)
        }
        assert not offending, f"{f} imports network library at module level: {offending}"


def test_I2_direct_text_preprocessing_survives_no_network(tmp_path, monkeypatch):
    """Runtime guard: with socket creation itself disabled, the direct-text chew path (used for
    .txt/.md/etc, no Docling/OCR involved) still succeeds end to end. This is the closest thing to
    proof that a source document never needs the network to be chewed."""
    def _blocked(*a, **k):
        raise AssertionError("network access attempted during local preprocessing")

    monkeypatch.setattr(socket, "socket", _blocked)
    monkeypatch.setattr(socket, "create_connection", _blocked)

    doc = tmp_path / "note.txt"
    doc.write_text("hello from a source document\n", encoding="utf-8")

    result = preprocess.process_direct_text(doc)
    assert result["pages"][0]["markdown"] == "hello from a source document\n"
    assert result["metadata"]["source_type"] == "direct_text"


# ── I3 — skills/prompts are global, never per-vault; prompts never leak into the index ────

def test_I3_prompt_templates_never_appear_in_skills_catalog():
    """prompts/ holds prompt *templates* (extract/synthesis/briefing/etc instructions to the
    model) — a structurally different thing from record skills (domain knowledge loaded by
    /ingest). Nothing under src/watchdog/prompts/ should ever surface as a catalog entry or in
    the classifier index text handed to the model."""
    prompt_names = {
        p.stem for p in (_SRC / "prompts").iterdir()
        if p.name.endswith(".md") and not p.name.startswith("_")
    }
    assert prompt_names, "expected at least one prompt template to check against"

    catalog_names = set(sc.catalog().keys())
    assert not (prompt_names & catalog_names)

    index = sc.build_index()
    for name in prompt_names:
        assert f"`{name}.md`" not in index


def test_I3_fresh_vault_copies_no_record_skills_directory(tmp_path):
    """Creating a vault installs the per-vault Claude Code command skills (workflow slash
    commands like /watchdog-query) but must never copy the global record-skills directory
    (src/watchdog/skills/records/) onto disk anywhere under the vault — those stay
    package-resident and are read live via watchdog.skills_catalog (D21)."""
    from watchdog.setup_cmd import install_skills

    commands_dir = tmp_path / "vault" / ".claude" / "commands"
    install_skills(commands_dir)

    installed = {p.name for p in commands_dir.glob("*.md")}
    assert installed, "expected install_skills to write at least one command file"

    record_skill_names = {
        p.name for p in (importlib.resources.files("watchdog") / "skills" / "records").iterdir()
        if p.name.endswith(".md")
    }
    assert not (installed & record_skill_names)
    assert not (tmp_path / "vault" / ".claude" / "commands" / "records").exists()
    # no "records" directory should exist anywhere under the installed vault scaffold
    assert not any(p.name == "records" for p in (tmp_path / "vault").rglob("*") if p.is_dir())


# ── I4 — configured model and effort only; no automatic escalation ───────────

class _FakeBackend:
    def __init__(self, *outputs):
        self.outputs = list(outputs)
        self.calls = []

    async def __call__(self, prompt, model_id, schema, api_key, max_tokens, effort=None):
        self.calls.append({"model_id": model_id, "effort": effort})
        return self.outputs.pop(0)


def test_I4_retry_after_invalid_json_keeps_same_model_and_effort(monkeypatch):
    """A failed call retries on the *same* model at the *same* effort — never escalating either
    knob to recover. Feed acomplete_json one invalid-JSON response then a valid one and assert
    both attempts hit the identical model id and effort."""
    monkeypatch.setattr(mc.auth, "resolve_auth",
                        lambda *a, **k: {"mode": "api-key", "key": "sk-ant-x"})
    backend = _FakeBackend(
        {"text": "not json", "usage": {"input_tokens": 10}, "cost_usd": 0.01},
        {"text": '{"name": "Acme"}', "usage": {"input_tokens": 10}, "cost_usd": 0.01},
    )
    monkeypatch.setitem(mc._ABACKENDS, "claude-api", backend)

    schema = {"type": "object", "properties": {"name": {"type": "string"}},
              "required": ["name"], "additionalProperties": False}

    result = asyncio.run(mc.acomplete_json(
        task="extract", prompt="p", schema=schema, model="sonnet", effort="low"))

    assert result.attempts == 2
    assert len(backend.calls) == 2
    models = {c["model_id"] for c in backend.calls}
    efforts = {c["effort"] for c in backend.calls}
    assert len(models) == 1, f"model changed across retry attempts: {backend.calls}"
    assert len(efforts) == 1, f"effort changed across retry attempts: {backend.calls}"


# ── I5 — research output re-enters via _INCOMING/, never a direct vault write ─────────

def test_I5_research_deposit_never_writes_outside_incoming(tmp_path):
    """pipeline/research.py is the deterministic egress gate behind /watchdog-research — its
    model boundary is a separate, uncapturable Claude Code session (nothing to mock in-process),
    so this guards the Python side: deposit_one's only writes are the source document and its
    .yml sidecar under _INCOMING/. Pre-seed vault notes research must never touch, run a deposit,
    and assert none of them changed."""
    vault = tmp_path / "vault"
    sentinels = {
        "entities" / Path("person") / "jane-doe.md": "# Jane Doe\noriginal content\n",
        Path("timeline.md"): "# Timeline\noriginal content\n",
        Path("hot.md"): "# Hot\noriginal content\n",
        Path("context.md"): "# Context\noriginal content\n",
    }
    for rel, content in sentinels.items():
        p = vault / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")

    def _fetch(url, **kwargs):
        return b"plain text body", "text/plain", url

    path = research.deposit_one(vault, "https://example.com/doc", title="A Doc",
                                fetcher=_fetch)

    assert path.parent == vault / "_INCOMING"
    for rel, content in sentinels.items():
        assert (vault / rel).read_text(encoding="utf-8") == content, \
            f"{rel} was modified by a research deposit"
