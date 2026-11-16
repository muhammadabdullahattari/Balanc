"""Score a versioned, labeled P2 benchmark dataset without exposing source rows."""

import argparse
import hashlib
import json
import time
import tracemalloc
from decimal import Decimal
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, ValidationError


class ForecastCase(BaseModel):
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    predicted: Decimal = Field(allow_inf_nan=False)
    actual: Decimal = Field(allow_inf_nan=False)


class PostingCase(BaseModel):
    expected: str | None
    proposed: str | None


class CostCase(BaseModel):
    pages: int = Field(gt=0)
    cost_usd: Decimal = Field(ge=0, allow_inf_nan=False)


class OcrCase(BaseModel):
    expected_text: str
    extracted_text: str
    expected_fields: dict[str, str] = Field(default_factory=dict)
    extracted_fields: dict[str, str] = Field(default_factory=dict)


class MatchingCase(BaseModel):
    bank_row_id: str
    expected_match_id: str | None = None
    proposed_match_id: str | None = None


class AnomalyCase(BaseModel):
    transaction_id: str
    is_anomaly: bool
    flagged: bool


class Baseline(BaseModel):
    forecast_mae_max: dict[str, Decimal] = Field(default_factory=dict)
    posting_accuracy_min: Decimal | None = Field(default=None, ge=0, le=1)
    usd_per_page_max: Decimal | None = Field(default=None, ge=0, allow_inf_nan=False)
    ocr_character_accuracy_min: Decimal | None = Field(default=None, ge=0, le=1)
    matching_f1_min: Decimal | None = Field(default=None, ge=0, le=1)
    anomaly_precision_min: Decimal | None = Field(default=None, ge=0, le=1)


class Measurement(BaseModel):
    operation: str = Field(min_length=1, max_length=80)
    elapsed_seconds: Decimal = Field(ge=0, allow_inf_nan=False)
    peak_memory_bytes: int | None = Field(default=None, ge=0)
    cpu_seconds: Decimal | None = Field(default=None, ge=0, allow_inf_nan=False)


class Dataset(BaseModel):
    version: Literal[1]
    dataset_id: str = Field(min_length=1, max_length=100)
    sample: Literal["synthetic", "representative"]
    forecasts: list[ForecastCase] = Field(default_factory=list, max_length=10000)
    postings: list[PostingCase] = Field(default_factory=list, max_length=10000)
    costs: list[CostCase] = Field(default_factory=list, max_length=10000)
    ocr: list[OcrCase] = Field(default_factory=list, max_length=10000)
    matching: list[MatchingCase] = Field(default_factory=list, max_length=10000)
    anomalies: list[AnomalyCase] = Field(default_factory=list, max_length=10000)
    baseline: Baseline = Field(default_factory=Baseline)
    measurements: list[Measurement] = Field(default_factory=list, max_length=10000)


def score(raw: bytes) -> dict[str, object]:
    dataset = Dataset.model_validate_json(raw)
    errors: dict[str, list[Decimal]] = {}
    for row in dataset.forecasts:
        errors.setdefault(row.currency, []).append(abs(row.predicted - row.actual))
    count = len(dataset.postings)
    correct = sum(row.expected == row.proposed for row in dataset.postings)
    proposed = sum(row.proposed is not None for row in dataset.postings)
    incorrect = sum(
        row.proposed is not None and row.proposed != row.expected for row in dataset.postings
    )
    pages = sum(row.pages for row in dataset.costs)
    cost = sum((row.cost_usd for row in dataset.costs), Decimal(0))
    accuracy = Decimal(correct) / count if count else None
    cost_per_page = cost / pages if pages else None
    forecast_mae = {
        currency: sum(values, Decimal(0)) / len(values) for currency, values in errors.items()
    }
    checks: list[dict[str, str | None]] = []

    def compare(
        metric: str, observed: Decimal | None, target: Decimal, *, minimum: bool = False
    ) -> None:
        if not target.is_finite() or target < 0:
            raise ValueError("Invalid baseline target.")
        passes = observed is not None and (observed >= target if minimum else observed <= target)
        checks.append(
            {
                "metric": metric,
                "observed": str(observed) if observed is not None else None,
                "target": str(target),
                "status": "missing" if observed is None else ("pass" if passes else "fail"),
            }
        )

    for currency, target in sorted(dataset.baseline.forecast_mae_max.items()):
        compare(f"forecast_mae_{currency}", forecast_mae.get(currency), target)
    if dataset.baseline.posting_accuracy_min is not None:
        compare("posting_accuracy", accuracy, dataset.baseline.posting_accuracy_min, minimum=True)
    if dataset.baseline.usd_per_page_max is not None:
        compare("usd_per_page", cost_per_page, dataset.baseline.usd_per_page_max)

    # OCR Evaluation
    ocr_count = len(dataset.ocr)
    ocr_exact = sum(1 for c in dataset.ocr if c.expected_text.strip() == c.extracted_text.strip())
    total_fields = sum(len(c.expected_fields) for c in dataset.ocr)
    matching_fields = sum(
        sum(1 for k, v in c.expected_fields.items() if c.extracted_fields.get(k) == v)
        for c in dataset.ocr
    )
    ocr_character_accuracy = Decimal(ocr_exact) / ocr_count if ocr_count else None
    ocr_field_accuracy = Decimal(matching_fields) / total_fields if total_fields else None

    # Matching Evaluation (Bank Reconciliation)
    matching_count = len(dataset.matching)
    m_tp = sum(
        1
        for m in dataset.matching
        if m.expected_match_id and m.expected_match_id == m.proposed_match_id
    )
    m_fp = sum(
        1
        for m in dataset.matching
        if m.proposed_match_id and m.proposed_match_id != m.expected_match_id
    )
    m_fn = sum(
        1
        for m in dataset.matching
        if m.expected_match_id and m.proposed_match_id != m.expected_match_id
    )
    matching_accuracy = (
        Decimal(sum(1 for m in dataset.matching if m.expected_match_id == m.proposed_match_id))
        / matching_count
        if matching_count
        else None
    )
    matching_precision = Decimal(m_tp) / (m_tp + m_fp) if (m_tp + m_fp) else None
    matching_recall = Decimal(m_tp) / (m_tp + m_fn) if (m_tp + m_fn) else None
    matching_f1 = (
        (2 * matching_precision * matching_recall) / (matching_precision + matching_recall)
        if (matching_precision and matching_recall and (matching_precision + matching_recall) > 0)
        else None
    )

    # Anomaly Detection Evaluation
    anomaly_count = len(dataset.anomalies)
    a_tp = sum(1 for a in dataset.anomalies if a.is_anomaly and a.flagged)
    a_fp = sum(1 for a in dataset.anomalies if not a.is_anomaly and a.flagged)
    a_fn = sum(1 for a in dataset.anomalies if a.is_anomaly and not a.flagged)
    a_tn = sum(1 for a in dataset.anomalies if not a.is_anomaly and not a.flagged)
    anomaly_precision = Decimal(a_tp) / (a_tp + a_fp) if (a_tp + a_fp) else None
    anomaly_recall = Decimal(a_tp) / (a_tp + a_fn) if (a_tp + a_fn) else None
    anomaly_fpr = Decimal(a_fp) / (a_fp + a_tn) if (a_fp + a_tn) else None

    if dataset.baseline.ocr_character_accuracy_min is not None:
        compare(
            "ocr_character_accuracy",
            ocr_character_accuracy,
            dataset.baseline.ocr_character_accuracy_min,
            minimum=True,
        )
    if dataset.baseline.matching_f1_min is not None:
        compare("matching_f1", matching_f1, dataset.baseline.matching_f1_min, minimum=True)
    if dataset.baseline.anomaly_precision_min is not None:
        compare(
            "anomaly_precision",
            anomaly_precision,
            dataset.baseline.anomaly_precision_min,
            minimum=True,
        )

    measurements: dict[str, list[Measurement]] = {}
    for measurement in dataset.measurements:
        measurements.setdefault(measurement.operation, []).append(measurement)
    return {
        "runner_version": 2,
        "dataset_id": dataset.dataset_id,
        "sample": dataset.sample,
        "dataset_sha256": hashlib.sha256(raw).hexdigest(),
        "forecast": {
            currency: {
                "count": len(values),
                "mean_absolute_error": str(sum(values, Decimal(0)) / len(values)),
            }
            for currency, values in sorted(errors.items())
        },
        "posting": {
            "count": count,
            "correct": correct,
            "proposed": proposed,
            "incorrect_proposals": incorrect,
            "accuracy": str(Decimal(correct) / count) if count else None,
            "proposal_error_rate": str(Decimal(incorrect) / proposed) if proposed else None,
        },
        "ocr": {
            "count": ocr_count,
            "exact_matches": ocr_exact,
            "character_accuracy": str(ocr_character_accuracy) if ocr_character_accuracy else None,
            "field_accuracy": str(ocr_field_accuracy) if ocr_field_accuracy else None,
        },
        "matching": {
            "count": matching_count,
            "accuracy": str(matching_accuracy) if matching_accuracy else None,
            "precision": str(matching_precision) if matching_precision else None,
            "recall": str(matching_recall) if matching_recall else None,
            "f1_score": str(matching_f1) if matching_f1 else None,
        },
        "anomaly": {
            "count": anomaly_count,
            "precision": str(anomaly_precision) if anomaly_precision else None,
            "recall": str(anomaly_recall) if anomaly_recall else None,
            "false_positive_rate": str(anomaly_fpr) if anomaly_fpr else None,
        },
        "baseline_checks": checks,
        "supplied_pipeline_measurements": {
            operation: {
                "count": len(rows),
                "total_elapsed_seconds": str(sum(row.elapsed_seconds for row in rows)),
                "peak_memory_bytes": max(
                    (r.peak_memory_bytes for r in rows if r.peak_memory_bytes is not None),
                    default=None,
                ),
                "total_cpu_seconds": str(
                    sum(r.cpu_seconds for r in rows if r.cpu_seconds is not None)
                )
                if all(r.cpu_seconds is not None for r in rows)
                else None,
            }
            for operation, rows in sorted(measurements.items())
        },
        "cost": {
            "pages": pages,
            "total_usd": str(cost),
            "usd_per_page": str(cost / pages) if pages else None,
        },
    }


def present(report: dict[str, object]) -> str:
    lines = [
        f"Benchmark {report['dataset_id']} ({report['sample']})",
        f"Dataset SHA-256: {report['dataset_sha256']}",
    ]
    for heading, key in (
        ("Forecast error by currency", "forecast"),
        ("Posting quality", "posting"),
        ("OCR extraction quality", "ocr"),
        ("Bank matching metrics", "matching"),
        ("Anomaly detection metrics", "anomaly"),
        ("USD processing cost", "cost"),
        ("Pipeline observations supplied by dataset", "supplied_pipeline_measurements"),
        ("Scoring runner measurements", "runner_measurements"),
        ("Baseline checks", "baseline_checks"),
    ):
        lines.extend(("", heading, json.dumps(report.get(key), indent=2)))
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--output", type=Path, help="Write a JSON report to a new file.")
    parser.add_argument(
        "--check", action="store_true", help="Fail if any configured baseline is unmet."
    )
    parser.add_argument("--format", choices=("json", "text"), default="json")
    args = parser.parse_args()
    with args.dataset.open("rb") as source:
        raw = source.read(8_000_001)
    if len(raw) > 8_000_000:
        parser.error("Dataset exceeds 8 MB.")
    try:
        tracemalloc.start()
        wall_start = time.perf_counter()
        cpu_start = time.process_time()
        try:
            report = score(raw)
            report["runner_measurements"] = {
                "elapsed_seconds": time.perf_counter() - wall_start,
                "cpu_seconds": time.process_time() - cpu_start,
                "peak_python_allocation_bytes": tracemalloc.get_traced_memory()[1],
            }
        finally:
            tracemalloc.stop()
    except (ValidationError, ValueError, ArithmeticError):
        parser.error("Dataset does not match the P2 benchmark schema.")
    content = present(report) if args.format == "text" else json.dumps(report, indent=2)
    if args.output:
        with args.output.open("x", encoding="utf-8") as output:
            output.write(content + "\n")
    else:
        print(content)
    if args.check:
        checks = report["baseline_checks"]
        if (
            not isinstance(checks, list)
            or not checks
            or any(row["status"] != "pass" for row in checks)
        ):
            parser.exit(1, "Benchmark baselines are missing or unmet.\n")


if __name__ == "__main__":
    main()
