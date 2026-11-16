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


class Baseline(BaseModel):
    forecast_mae_max: dict[str, Decimal] = Field(default_factory=dict)
    posting_accuracy_min: Decimal | None = Field(default=None, ge=0, le=1)
    usd_per_page_max: Decimal | None = Field(default=None, ge=0, allow_inf_nan=False)


class Measurement(BaseModel):
    operation: str = Field(min_length=1, max_length=80)
    elapsed_seconds: Decimal = Field(ge=0, allow_inf_nan=False)
    peak_memory_bytes: int | None = Field(default=None, ge=0)
    cpu_seconds: Decimal | None = Field(default=None, ge=0, allow_inf_nan=False)


class Dataset(BaseModel):
    version: Literal[1]
    dataset_id: str = Field(min_length=1, max_length=100)
    sample: Literal["synthetic", "representative"]
    forecasts: list[ForecastCase] = Field(max_length=10000)
    postings: list[PostingCase] = Field(max_length=10000)
    costs: list[CostCase] = Field(max_length=10000)
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
