# ADR-006: Durable job delivery

**Date:** 2026-10-27  
**Status:** Accepted  
**Source:** LedgerPilot SRS, sections 7, 8, 14

## Decision

- Commit a job's business intent and outbox event in the same PostgreSQL transaction when asynchronous delivery is required.
- A dispatcher publishes committed events to the configured queue. Consumers use an idempotent operation/inbox key and acknowledge only after durable result commit.
- SQS is the production delivery service in the SRS architecture. Local development and the quota-limited demo use the simplest durable profile supported by the SRS; they do not require Redis Streams.
- Assume at-least-once delivery. Use bounded retries with jitter, visibility/lease rules, dead-letter capture, audited replay, and provider idempotency or status reconciliation for ambiguous external outcomes.
- Queue outages must not silently lose work or corrupt posted accounting. Preserve review state and return explicit availability errors when durability cannot be guaranteed.

## Consequences

Message delivery can be repeated without duplicating financial effects. Queue choice remains separate from ledger semantics and is not an excuse to claim free production availability.
