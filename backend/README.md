# LedgerPilot API

Python package for the LedgerPilot FastAPI modular monolith. Run it from this directory with `uv run uvicorn ledgerpilot.main:app --reload`.

Phase 2 implements identity, tenant isolation, durable journal drafts, posting, reversals and ledger reporting. Follow [local setup and operational requirements](../docs/phase-2-backend-foundation.md) before starting the API, including PostgreSQL migrations and the local SMTP mailbox. Browser authentication requires HTTPS; cookie attributes are intentionally secure in local configuration too.

See the root [README](../README.md#ci-and-deployments) for CI checks, Vercel web deployment, and the AWS demo deployment setup.

Phase 3 adds trade, banking, operational reports, private evidence, scoped workspace administration and reviewed CSV migration. See the [implementation record](../docs/phase-3-business-modules.md), [API coverage inventory](../docs/contracts/phase-3-api-coverage.md) and [extraction contract](../docs/contracts/document-extraction.md). Phase status and remaining closure items are recorded explicitly; these local modules do not establish production capacity.
