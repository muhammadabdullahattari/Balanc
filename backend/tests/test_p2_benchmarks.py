import json

from ledgerpilot.workspace.benchmarks import score


def test_scores_forecasts_posting_abstentions_and_weighted_cost() -> None:
    result = score(
        json.dumps(
            {
                "version": 1,
                "dataset_id": "synthetic-v1",
                "sample": "synthetic",
                "forecasts": [{"currency": "USD", "predicted": "100", "actual": "90"}],
                "postings": [
                    {"expected": None, "proposed": None},
                    {"expected": "a", "proposed": "b"},
                ],
                "costs": [{"pages": 1, "cost_usd": "0.01"}, {"pages": 9, "cost_usd": "0.04"}],
            }
        ).encode()
    )
    assert result["forecast"] == {"USD": {"count": 1, "mean_absolute_error": "10"}}
    assert result["posting"] == {
        "count": 2,
        "correct": 1,
        "proposed": 1,
        "incorrect_proposals": 1,
        "accuracy": "0.5",
        "proposal_error_rate": "1",
    }
    assert result["cost"] == {"pages": 10, "total_usd": "0.05", "usd_per_page": "0.005"}


def test_configurable_baseline_reports_failures_and_missing_observations() -> None:
    report = score(
        json.dumps(
            {
                "version": 1,
                "dataset_id": "synthetic",
                "sample": "synthetic",
                "forecasts": [],
                "postings": [{"expected": "A", "proposed": "B"}],
                "costs": [],
                "baseline": {"forecast_mae_max": {"USD": "1"}, "posting_accuracy_min": "0.9"},
                "measurements": [
                    {"operation": "fixture", "elapsed_seconds": "1.2", "peak_memory_bytes": 100}
                ],
            }
        ).encode()
    )
    assert report["baseline_checks"] == [
        {"metric": "forecast_mae_USD", "observed": None, "target": "1", "status": "missing"},
        {"metric": "posting_accuracy", "observed": "0", "target": "0.9", "status": "fail"},
    ]
    assert report["supplied_pipeline_measurements"] == {
        "fixture": {
            "count": 1,
            "total_elapsed_seconds": "1.2",
            "peak_memory_bytes": 100,
            "total_cpu_seconds": None,
        },
    }
