# Opening-balance CSV contract

Use the existing `/api/v1/businesses/{business_id}/imports/preview` and `/imports/apply` routes. Opening balances are outstanding balances at cutover, not the original face amount before historical payments. The selected policy is recorded in [ADR-012](../architecture/ADR-012-reviewed-opening-balances.md).

## Preparation

Create the chart, appropriate active party roles and an open period containing cutover. Use a business with no posted journals. Reconcile legacy source totals first. Choose an active, non-control equity account specifically approved for opening offsets. The API does not guess that account or plug unexplained differences into income.

Set request fields:

- `mode`: `opening`.
- `cutover_date`: the reviewed date on which the residual balances enter LedgerPilot.
- `opening_equity_account_code`: an existing equity account code.
- `review_reason`: the accountant's nonempty review explanation.
- `expected_trial`: account-code to exact signed debit-minus-credit strings for the complete resulting trial balance. Unspecified accounts must have zero balance.
- `content`: CSV, optionally with the existing `columns` mapping.

Required logical CSV columns are `kind,party_id,reference,original_date,due_date,amount,control_account_code`. An optional `currency` must equal business currency.

- `kind` is `customer`, `supplier`, `loan_payable` or `loan_receivable`.
- `party_id` must identify an active same-business customer, supplier, lender or borrower respectively.
- `control_account_code` must match AR asset, AP liability, loan-payable liability or loan-receivable asset respectively.
- `reference` is a unique source reference for that party/kind, maximum 80 plain characters.
- `original_date` must be on or before cutover. `due_date` must be on or after original date, and may precede cutover. Dates use ISO `YYYY-MM-DD`.
- `amount` is a strictly positive exact decimal string in the business currency's precision. It is trade outstanding principal/total or loan principal only. No cash, accrued-interest or tax allocation is inferred.

Positive customer/loan-receivable balances debit their control and credit equity. Supplier/loan-payable balances credit their control and debit equity. All effects post at cutover.

## Approval and reads

Preview returns row errors, compiled commands, the actual business version, `preview_hash`, `posted=false` and `required_approval_role=accountant`. Apply requires the identical previewed request plus `preview_hash`, a new `idempotency_key` and `expected_version`. Only an authenticated accountant with MFA can approve. The whole batch commits only when AR/AP/loan controls and supplied trial totals agree. Limits are 100 CSV rows and at most 25 opening source operations; each row is one operation.

`GET /opening-balances` provides bounded cursor pagination over immutable reviewer/source metadata. Trade-document reads distinguish original date from cutover using `opening_source_date`. Loan-movement reads identify opening principal as `kind=opening`. Signed evidence packs include `opening-balances.json` with exact monetary strings and review metadata.

Payments and loan repayments after cutover use the ordinary APIs. Do not enter the opening source again as a current invoice, bill or bank deposit. Same-key retries return the initial result; repeated source files are rejected under another key. A second opening batch and imports into already-posted books are refused. Corrections require source-aware review, never deletion or an independent GL reversal.

## Cash reporting and later corrections

Opening trade totals do not supply historic cash-recognition or tax treatment. Cash-basis reports return `ERR_CASH_POLICY` until an accountant approves an evidence-backed supported policy. Recognition on settlement uses exact approved income/expense weights; previously recognized amounts create no new profit. The accountant must explicitly confirm no tax component or separately accounted tax. Unsupported tax/recognition cases still require complete history. See [the completion API contract](phase-3-completion.md).

Linked opening corrections are now available through `/opening-balances/{id}/corrections` and `/history`; originals remain immutable. Trade increases/decreases create sourced opening documents/credits; principal adjustments preserve loan controls and cannot make principal negative. Correction dates cannot predate posted activity, and retry keys are bound to the target opening balance.
