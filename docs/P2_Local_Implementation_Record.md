# P2 local implementation record

The local preparation track follows the [next phases plan](Next_Phases_Implementation_Plan.md#what-to-build-now). The P1 source reference is commit `c28c360bc2e7d4f298ef2d0307ae040ed009b0a8` with the existing working-tree frontend and authentication changes applied. This identifies the starting tree. It is separate from hosted Phase 9 and Phase 10 acceptance evidence.

## L1 P1 baseline

The frontend production build, TypeScript check and 13 focused frontend tests passed. The service-backed backend suite passed 42 tests in 121.50 seconds with disposable PostgreSQL databases and Mailpit. It covered the Phase 2 integration, Phase 4 verification and identity hardening suites. Backend lint, formatting and strict type checking passed. Hosted browser and live Google consent checks remain pending.

## L2 First foundation slice

- Reused the PostgreSQL evidence job ID and lease lifecycle. The worker now logs elapsed processing time and the result code alongside the existing tenant and job IDs. A database failure after claim also carries the job ID. The structured logger accepts duration as a numeric field and continues to filter free-form and sensitive values.
- Extended the existing synthetic database restore drill to include a real uploaded, encrypted evidence original. The drill copies encrypted evidence from a quiet test workspace to a separate backup and restored location. It checks the restored hash, decryption, queued job association, tenant-visible evidence metadata and quarantine behavior alongside the ledger balances and row-level security checks.
- The extended drill passed as part of the 42-test local run, using the existing PostgreSQL container's `pg_dump` and `pg_restore`.
- This drill uses a synthetic encryption key and a local copy. Hosted recovery still needs a coordinated snapshot method, independent key recovery and agreed RTO and RPO. Record elapsed restore time, backup age and financial and evidence reconciliation in that exercise.

## Next implementation checkpoint

Define the P2 email rollout gate with a default-off tenant allowlist before the first delivery path can send. Reuse the existing PostgreSQL transaction and job conventions. The worker restart exercise should prove stale leases cannot complete reclaimed work. Build the email outbox and Mailpit workflow after those controls are in place.

Design the outbox around stable delivery IDs, consent and business-state rechecks before send, and bounded attempts. For ambiguous SMTP outcomes, use a review or reconciliation state. A timeout after SMTP acceptance is insufficient evidence to resend safely. The hosted queue and production mail provider remain later decisions.
