# Empirical AI Accuracy & Extraction Benchmarks (`ledgerpilot.eval.run`)

All numbers in [`latest_eval_results.json`](latest_eval_results.json) are generated empirically by running the live extraction, bounding-box grounding, multi-invoice subset-sum bundler, and sequence-gap engines over the versioned ground-truth corpus in `backend/src/ledgerpilot/eval/dataset.py`.

## Running the Benchmark Suite Locally

```powershell
cd backend
uv run python -m ledgerpilot.eval.run --output ../docs/benchmarks/latest_eval_results.json
```

If any metric drops below `BASELINE_THRESHOLDS`, `python -m ledgerpilot.eval.run` exits with code `1`.

## Baseline Gates & Latest Measured Results

| Capability | Corpus Coverage | Baseline Gate | Measured Result |
| :--- | :--- | :--- | :--- |
| Document Field Extraction (`F1`) | English, French, German, Urdu, thermal receipts, credit notes | `>= 0.9400` | `1.0000` |
| Pixel Bounding-Box Grounding (`Mean IoU`) | Normalized `[0..1]` total amount regions | `>= 0.9000` | `1.0000` |
| Payment-to-Invoice Matching | `$1,240` 3-invoice bundle, processor fee deduction, EUR->USD FX settlement | `1.0000` (`< 200 ms`) | `1.0000` (`< 5 ms`) |
| Document Sequence-Gap Anomaly (`RULE-ANOM-002`) | Missing invoice numbers vs. contiguous control series | `1.0000` | `1.0000` |
