# Chaos Drill 01 — Evidence Object Storage Loss & Ciphertext Tampering

## Scenario & Fault Injected
- **Fault:** Simulated missing local replica blob and single-byte bit-flip tampering inside encrypted `LPENC1` / `LPENV1` evidence payloads on disk (`backend/tests/test_chaos_and_resilience.py`).
- **Expected Invariant:** LedgerPilot must **never** serve unverified, truncated, or corrupted bytes into auditor packs or document previews, and must recover transparently from shared S3 when a local replica cache misses.

## Observed Behavior
1. Deleting or corrupting any byte of an encrypted chunk triggers `InvalidToken` or SHA-256 digest mismatch, raising `DomainError("ERR_STORAGE_UNAVAILABLE" | "ERR_INTEGRITY", status=503)`.
2. `exports.pack()` aborts pack generation (`export.evidence_failed` telemetry emitted) rather than producing an incomplete auditor ZIP.
3. Posted `app.journals` and `app.journal_lines` remain 100% balanced and untouched.

## Verification
- Automated in `backend/tests/test_chaos_and_resilience.py::test_chaos_01_storage_corruption_and_missing_blob_rejected`.
