# Local Phase 3 completion APIs

Paths are relative to `/api/v1/businesses/{business_id}`. Authentication, verified business membership, CSRF for writes and existing roles remain mandatory. OpenAPI provides the concrete request/response/error schemas.

## Duplicate review and correction suggestions

- `POST /evidence/{id}/identity`: supply expected identity version, reviewed supplier/date/total/currency and a reason. Identity revisions are append-only and do not alter original evidence.
- `GET /evidence/{id}/identity`: fetch the latest reviewed identity and version.
- `GET /evidence/{id}/duplicate-candidates`: exact content-hash or normalized reviewed business-identity matches, with reasons and `automatic_merge=false`. Originals must have passed scanning.
- `GET /bank-rules/correction-suggestions`: suggestions supported by at least two distinct corrected rows, using their latest actual journal order and an active income/expense account. Use the ordinary rule creation, preview and approval APIs to activate anything.

## Resume an export

1. `POST /exports/snapshots` with `idempotency_key`, `format` (`journals_csv` or `evidence_zip`), `start` and `end`. The response is `state=ready`, with ID, byte size, SHA-256, expiry, business version and range limit. If a source/size limit prevents building, the request fails without a misleading ready result.
2. `GET /exports/snapshots/{id}` retrieves authorized metadata. It returns 410 after expiry; artifacts removed by expiry cleanup return 404.
3. `GET /exports/snapshots/{id}/download` supports `Range: bytes=0-1048575` and subsequent ranges, plus `If-Match: "<sha256>"`. A valid range returns 206 and `Content-Range`; a full download returns 200. Mismatched ETag returns 412 and invalid/oversized ranges return 416. Multipart/suffix ranges are outside this bounded API.
4. Reassemble the bytes and compare the full SHA-256. New journal activity does not change the retained artifact. Do not mix ranges from different export IDs. Browser CORS exposes ETag/range headers and permits Range/If-Match request headers.

Snapshots expire after 24 hours, are encrypted at rest, remain private to the creator and are limited to three live artifacts per member/business, 10 MiB each. Requests always need current membership/session authorization. Trial expiry retains export access. Expired artifacts are cleaned on a subsequent successful creation; no posted history is deleted.

## Correct an opening balance

`POST /opening-balances/{id}/corrections` requires an accountant, idempotency key, expected business version, correction date, increase/decrease, positive amount and reason. For a trade decrease, optional `source_document_id` selects a supplemental opening invoice/bill in this opening history. Original source is the default. No correction may predate already-posted activity.

The correction posts only control/equity effects, with a linked source record, and preserves the original. Trade decreases create credits; overpaid credit can use the ordinary explicit credit/refund allocation workflow. Loan corrections change principal and refuse a negative balance. `GET /opening-balances/{id}/history` exposes exact deltas, source/journal IDs, reviewer reasons and cash policy.

## Approve cash recognition

`POST /opening-balances/{id}/cash-policy` requires an accountant, scanned supporting evidence, reason and explicit `tax_treatment` (`no_tax_component` or `separately_accounted`). Choose:

- `on_settlement`: provide distinct approved income/expense accounts and positive exact weights totaling the original opening amount. Cash allocation, not cutover posting, generates reported cash-basis profit.
- `previously_recognized`: components are empty and settlement does not generate new profit.

Policies are immutable; an identical retry returns the existing policy. Missing or incompatible policy fails explicitly. These are supported reporting treatments, not statutory filing or inferred tax history. Reports cite policy IDs; evidence packs include approval/correction records. Other tax/recognition cases require complete supported history or an approved future extension.

## Manual privacy fulfillment

`GET /privacy-review-queue` is owner/accountant only. `POST /privacy-requests/{id}/reviews` requires an independent reviewer, outcome (`export_ready`, `retained`, `requires_followup`), reason, policy reference and, when retaining/holding, retained categories. Optional `retain_until` and `legal_hold` record the supplied policy decision. An owner cannot approve their own request.

`GET /privacy-requests/{id}` provides requester/admin scoped decision history. Own-user request lists and `/privacy-export` show the current decision. Export-ready users download through their own MFA session. Read-only trials still permit these privacy operations. Every outcome says `deleted=false`: P1 records manual review and export fulfillment; P2 owns automated processor/backup/deletion execution.

## Frontend contracts

The OpenAPI artifact includes explicit response types, binary success responses, Range/If-Match parameters, standard error envelopes and stable operation IDs. `GET /reports/history` provides bounded snapshot metadata; `GET /reports/history/{id}` returns the immutable typed report. Numeric assistant answers accept only the allowed metric enum and an authorized saved report ID. Phase 4 reviews and generates the TypeScript client before frontend integration.
