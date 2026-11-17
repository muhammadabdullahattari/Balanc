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


def test_scores_ocr_matching_and_anomalies_with_baseline_checks() -> None:
    dataset = {
        "version": 1,
        "dataset_id": "comprehensive-p2",
        "sample": "representative",
        "ocr": [
            {
                "expected_text": "Invoice #101\nTotal: $100.00",
                "extracted_text": "Invoice #101\nTotal: $100.00",
                "expected_fields": {"inv": "101", "total": "100.00"},
                "extracted_fields": {"inv": "101", "total": "100.00"},
            },
            {
                "expected_text": "Receipt #500",
                "extracted_text": "Receipt #501",
                "expected_fields": {"rec": "500"},
                "extracted_fields": {"rec": "501"},
            },
        ],
        "matching": [
            {"bank_row_id": "b1", "expected_match_id": "m1", "proposed_match_id": "m1"},
            {"bank_row_id": "b2", "expected_match_id": "m2", "proposed_match_id": "m3"},
            {"bank_row_id": "b3", "expected_match_id": None, "proposed_match_id": None},
        ],
        "anomalies": [
            {"transaction_id": "t1", "is_anomaly": True, "flagged": True},
            {"transaction_id": "t2", "is_anomaly": False, "flagged": True},
            {"transaction_id": "t3", "is_anomaly": True, "flagged": False},
            {"transaction_id": "t4", "is_anomaly": False, "flagged": False},
        ],
        "baseline": {
            "ocr_character_accuracy_min": "0.5",
            "matching_f1_min": "0.4",
            "anomaly_precision_min": "0.4",
        },
    }
    result = score(json.dumps(dataset).encode())

    # OCR: 1 exact match out of 2 = 0.5 char accuracy;
    # 2 matching fields out of 3 = 0.6666... field accuracy
    assert result["ocr"]["count"] == 2
    assert result["ocr"]["exact_matches"] == 1
    assert result["ocr"]["character_accuracy"] == "0.5"
    assert result["ocr"]["field_accuracy"] is not None

    # Matching: 3 rows, 2 exact match expectations (m1==m1, None==None) -> accuracy = 2/3
    # TP = 1 (m1==m1), FP = 1 (m3 != m2), FN = 1 (m2 != m3)
    # Precision = 1/(1+1) = 0.5, Recall = 1/(1+1) = 0.5, F1 = 0.5
    assert result["matching"]["count"] == 3
    assert result["matching"]["precision"] == "0.5"
    assert result["matching"]["recall"] == "0.5"
    assert result["matching"]["f1_score"] == "0.5"

    # Anomalies:
    # TP = 1 (t1), FP = 1 (t2), FN = 1 (t3), TN = 1 (t4)
    # Precision = 1/(1+1) = 0.5, Recall = 1/(1+1) = 0.5, FPR = 1/(1+1) = 0.5
    assert result["anomaly"]["count"] == 4
    assert result["anomaly"]["precision"] == "0.5"
    assert result["anomaly"]["recall"] == "0.5"
    assert result["anomaly"]["false_positive_rate"] == "0.5"

    # Baseline checks: all three meet or exceed target
    assert result["baseline_checks"] == [
        {"metric": "ocr_character_accuracy", "observed": "0.5", "target": "0.5", "status": "pass"},
        {"metric": "matching_f1", "observed": "0.5", "target": "0.4", "status": "pass"},
        {"metric": "anomaly_precision", "observed": "0.5", "target": "0.4", "status": "pass"},
    ]
