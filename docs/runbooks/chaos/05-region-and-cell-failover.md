# Chaos Drill 05 — Regional Cell Residency Lock & Tenant Crypto-Shredding

## Scenario & Fault Injected
- **Fault 1:** Attempted cross-cell read/failover routing (`eu-central-fra-cell-1` -> `us-east-va-cell-1`) while `data_residency_locked = True`.
- **Fault 2:** Permanent destruction of a tenant's envelope DEK (`LPENV1`) both with and without an active `LegalHold`.
- **Expected Invariant:** Locked regional tenants reject cross-region routing (`403 ERR_RESIDENCY_LOCKED`); active legal holds block key destruction (`409 ERR_LEGAL_HOLD`); once destroyed after hold release, `LPENV1` ciphertexts become provably undecryptable (`410 ERR_KEY_SHREDDED`) with an `Ed25519`-signed certificate while other tenants' blobs remain readable.

## Observed Behavior
1. `CellRouter.resolve()` enforces `ERR_RESIDENCY_LOCKED` whenever `requested_cell != pinned_cell` and `residency_locked=True`.
2. `storage.get_tenant_envelope()` raises `410 ERR_KEY_SHREDDED` immediately after wrapped DEK destruction, while another business's `LPENV1` ciphertext decrypts cleanly under the same master KEK.

## Verification
- Automated in `backend/tests/test_chaos_and_resilience.py::test_chaos_05_cell_residency_lock_and_tenant_crypto_shredding`.
