"""CLI & programmatic runner for LedgerPilot's real-document eval harness."""

from __future__ import annotations

import argparse
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ledgerpilot.audit.tools import find_document_sequence_gaps
from ledgerpilot.banking.matching import MatchCandidateDoc, find_best_document_match
from ledgerpilot.documents.understanding import extract_structured_document
from ledgerpilot.eval.dataset import (
    BANK_MATCHING_CASES,
    CORPUS_VERSION,
    DOCUMENT_EXTRACTION_CASES,
    SEQUENCE_GAP_CASES,
)

BASELINE_THRESHOLDS = {
    "extraction_precision": 0.94,
    "extraction_recall": 0.94,
    "extraction_f1": 0.94,
    "mean_box_iou": 0.90,
    "bank_match_accuracy": 1.00,
    "sequence_gap_f1": 1.00,
}


def _box_iou(a: dict[str, Any] | None, b: dict[str, float] | None) -> float:
    if not a or not b:
        return 0.0
    ix1 = max(float(a["left"]), float(b["left"]))
    iy1 = max(float(a["top"]), float(b["top"]))
    ix2 = min(float(a["right"]), float(b["right"]))
    iy2 = min(float(a["bottom"]), float(b["bottom"]))
    inter = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
    area_a = max(0.0, float(a["right"]) - float(a["left"])) * max(
        0.0, float(a["bottom"]) - float(a["top"])
    )
    area_b = max(0.0, float(b["right"]) - float(b["left"])) * max(
        0.0, float(b["bottom"]) - float(b["top"])
    )
    union = area_a + area_b - inter
    return round(inter / union, 4) if union > 0 else 0.0


def run_evaluation_suite() -> dict[str, Any]:
    """Execute real extraction, bundle matching, and anomaly detection over the corpus."""
    started = time.perf_counter()

    tp = 0
    fp = 0
    fn = 0
    ious: list[float] = []
    doc_details: list[dict[str, Any]] = []

    checked_fields = (
        "document_type",
        "vendor_name",
        "document_number",
        "document_date",
        "currency",
        "subtotal_amount",
        "tax_amount",
        "total_amount",
        "arithmetic_consistent",
    )

    for case in DOCUMENT_EXTRACTION_CASES:
        pred = extract_structured_document(
            case["raw_text"],
            filename=case["filename"],
            default_currency="USD",
            ocr_words=case["ocr_words"],
            settings=None,
        )
        expected = case["expected"]
        case_correct = 0
        for fname in checked_fields:
            exp_val = expected.get(fname)
            got_val = pred.get(fname)
            if got_val is not None and str(got_val) != "":
                if str(got_val) == str(exp_val):
                    tp += 1
                    case_correct += 1
                else:
                    fp += 1
            elif exp_val is not None:
                fn += 1

        total_field_meta = next(
            (f for f in pred.get("fields", []) if f.get("name") == "total_amount"),
            None,
        )
        iou = _box_iou(total_field_meta, case.get("ground_truth_total_box"))
        ious.append(iou)
        doc_details.append(
            {
                "case_id": case["case_id"],
                "locale": case["locale"],
                "orientation": case["orientation"],
                "fields_matched": f"{case_correct}/{len(checked_fields)}",
                "total_box_iou": iou,
            }
        )

    precision = round(tp / (tp + fp), 4) if (tp + fp) else 0.0
    recall = round(tp / (tp + fn), 4) if (tp + fn) else 0.0
    f1 = round((2 * precision * recall) / (precision + recall), 4) if (precision + recall) else 0.0
    mean_iou = round(sum(ious) / len(ious), 4) if ious else 0.0

    # Bank matching cases (including 3-invoice $1,240 bundle, fee-adjusted, and FX settlement)
    match_hits = 0
    match_latencies_ms: list[float] = []
    for mcase in BANK_MATCHING_CASES:
        t0 = time.perf_counter()
        cands = [MatchCandidateDoc(**c) for c in mcase["candidates"]]
        proposal = find_best_document_match(
            mcase["statement_amount"],
            cands,
            statement_currency=mcase["statement_currency"],
            fx_rates=mcase["fx_rates"],
        )
        match_latencies_ms.append(round((time.perf_counter() - t0) * 1000, 3))
        if proposal is not None:
            got_nums = sorted(str(c["number"]) for c in proposal.components)
            if proposal.match_kind == mcase["expected_match_kind"] and got_nums == sorted(
                mcase["expected_numbers"]
            ):
                match_hits += 1

    match_accuracy = round(match_hits / len(BANK_MATCHING_CASES), 4)

    # Sequence-gap anomaly cases
    seq_hits = 0
    for scase in SEQUENCE_GAP_CASES:
        gaps = find_document_sequence_gaps(scase["documents"])
        missing: list[str] = []
        for g in gaps:
            missing.extend(g.get("missing_numbers", []))
        if sorted(missing) == sorted(scase["expected_missing"]):
            seq_hits += 1
    seq_f1 = round(seq_hits / len(SEQUENCE_GAP_CASES), 4)

    metrics = {
        "extraction_precision": precision,
        "extraction_recall": recall,
        "extraction_f1": f1,
        "mean_box_iou": mean_iou,
        "bank_match_accuracy": match_accuracy,
        "max_bundle_match_latency_ms": max(match_latencies_ms) if match_latencies_ms else 0.0,
        "sequence_gap_f1": seq_f1,
    }
    regressions = [
        f"{k}: got {metrics[k]} < baseline {thresh}"
        for k, thresh in BASELINE_THRESHOLDS.items()
        if float(metrics[k]) < thresh
    ]
    if metrics["max_bundle_match_latency_ms"] > 200.0:
        regressions.append(
            f"max_bundle_match_latency_ms: {metrics['max_bundle_match_latency_ms']}ms > 200ms"
        )

    return {
        "corpus_version": CORPUS_VERSION,
        "execution_provenance": "empirical_live_harness",
        "evaluated_at": datetime.now(UTC).isoformat(),
        "duration_ms": round((time.perf_counter() - started) * 1000, 2),
        "sample_counts": {
            "documents": len(DOCUMENT_EXTRACTION_CASES),
            "bank_matches": len(BANK_MATCHING_CASES),
            "sequence_gap_checks": len(SEQUENCE_GAP_CASES),
            "total": (
                len(DOCUMENT_EXTRACTION_CASES) + len(BANK_MATCHING_CASES) + len(SEQUENCE_GAP_CASES)
            ),
        },
        "metrics": metrics,
        "baseline_thresholds": BASELINE_THRESHOLDS,
        "gate_passed": len(regressions) == 0,
        "regressions": regressions,
        "document_cases": doc_details,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run LedgerPilot empirical AI benchmark suite.")
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Optional path to write JSON benchmark report.",
    )
    args = parser.parse_args(argv)
    report = run_evaluation_suite()
    serialized = json.dumps(report, indent=2)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized + "\n", encoding="utf-8")
    print(serialized)
    return 0 if report["gate_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
