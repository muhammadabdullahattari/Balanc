# Chaos Drill 04 — External AI Model Provider Outage (`503` / Timeout)

## Scenario & Fault Injected
- **Fault:** Forced `GroqModelAdapter` timeout / HTTP `503` unavailability during document extraction (`understand`), bank matching (`AI-01`), cash forecasting (`AI-08`), and causal Q&A (`/insights/question`).
- **Expected Invariant:** Core bookkeeping, document field extraction, multi-invoice matching, and cash forecasting remain 100% operational with deterministic provenance (`fallback_used = True`), zero 500 errors, and zero fabricated citations.

## Observed Behavior
1. `extract_structured_document()` falls back seamlessly to deterministic regex + OCR word-box grounding (`arithmetic_consistent` check preserved).
2. `find_best_document_match()` resolves 1-to-1, 3-invoice bundles, fee-deducted payouts, and cross-currency FX matches in `< 5 ms` without requiring external LLM availability.
3. All manual journal, invoice, bill, reconciliation, and auditor-pack endpoints have zero runtime dependency on external AI providers.

## Verification
- Automated in `backend/tests/test_chaos_and_resilience.py::test_chaos_04_model_provider_outage_graceful_fallback`.
