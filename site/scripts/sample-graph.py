"""Write the sample investigation's network to site/src/data/graph.json, for the website's
interactive graph. Builds the Port Calder sample (prepared answers, no model, no keys) in a
temporary folder, exports it, and keeps each entity's name, type and document count, and each
related pair once with its relationship wordings.

Run from the repository root with a Python that has Watchdog installed (see CLAUDE.md's dev setup):
    .venv/bin/python site/scripts/sample-graph.py
"""

import csv
import json
import tempfile
from collections import defaultdict
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "src" / "data" / "graph.json"


class _Quiet:
    def __getattr__(self, _name):
        return lambda *a, **k: None


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        import watchdog.config as config
        config.HOME = Path(tmp) / "home"
        from watchdog.ops import export, sample

        built = sample.create(_Quiet(), dir=str(Path(tmp) / "inv"))
        export.export(_Quiet(), Path(built["path"]), output=str(Path(tmp) / "export"))
        nodes = [
            {"id": r[":ID"], "name": r["name"], "type": r["type"], "docs": int(r["doc_count:int"])}
            for r in csv.DictReader(open(Path(tmp) / "export" / "nodes.csv"))
        ]
        pairs: dict[tuple[str, str], list[str]] = defaultdict(list)
        for r in csv.DictReader(open(Path(tmp) / "export" / "relationships.csv")):
            key = (r[":START_ID"], r[":END_ID"])
            if r[":TYPE"] not in pairs[key]:
                pairs[key].append(r[":TYPE"])
    linked = {n for pair in pairs for n in pair}
    graph = {
        "nodes": [n for n in nodes if n["id"] in linked],
        "links": [{"source": a, "target": b, "labels": labels} for (a, b), labels in pairs.items()],
    }
    OUT.write_text(json.dumps(graph, indent=1, ensure_ascii=False) + "\n")
    print(f"{len(graph['nodes'])} entities, {len(graph['links'])} relationships -> {OUT}")


if __name__ == "__main__":
    main()
