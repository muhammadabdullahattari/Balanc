"""Reject missing requirements, stale source references and nonexistent evidence pointers."""

import ast
import hashlib
import json
from pathlib import Path


def main() -> None:
    root = Path(__file__).resolve().parents[3]
    matrix = json.loads((root / "docs/contracts/phase-4-traceability.json").read_text("utf-8"))
    source = root / matrix["source"]
    if hashlib.sha256(source.read_bytes()).hexdigest() != matrix["source_sha256"]:
        raise SystemExit("SRS changed: review and update Phase 4 traceability")
    spec = json.loads((root / "docs/contracts/openapi.json").read_text("utf-8"))
    evidence = set()
    for path in (root / "backend/tests").glob("test_*.py"):
        for node in ast.walk(ast.parse(path.read_text("utf-8"))):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                evidence.add(f"{path.relative_to(root).as_posix()}::{node.name}")
    expected = {f"FR-{i:03}" for i in range(1, 45)} | {
        f"{kind}-{i:03}" for kind in ("VAL", "NFR", "SEC") for i in range(1, 21)
    }
    records = matrix["requirements"]
    if len(records) != len(expected) or {r["id"] for r in records} != expected:
        raise SystemExit("Missing or duplicate SRS requirement IDs")
    for row in records:
        if not row["expected"] or not row["note"] or not row["tc"]:
            raise SystemExit(f"Incomplete disposition: {row['id']}")
        if any(path not in spec["paths"] for path in row["routes"]):
            raise SystemExit(f"Unknown API path: {row['id']}")
        if any(test not in evidence for test in row["tests"]):
            raise SystemExit(f"Unknown test pointer: {row['id']}")
        if (
            row["status"] == "mapped_local_partial"
            and row["id"].startswith("FR-")
            and (not row["routes"] or not row["tests"] or not row.get("uc"))
        ):
            raise SystemExit(f"Missing local feature evidence: {row['id']}")
    print(f"Validated {len(records)} requirement references; this is not an acceptance verdict")


if __name__ == "__main__":
    main()
