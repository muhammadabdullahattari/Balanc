# ADR 013 — Local P1 completion controls

Date: 29 October 2026. Status: accepted within the user's instruction to implement the remaining Phase 3 work.

## Architecture retained

Keep the FastAPI modular monolith, existing identity/session service, canonical journal posting, forced tenant RLS and bounded PostgreSQL pools. No cloud resource, additional broker, public SDK, native mobile app or paid model service is introduced. Revision 0019 adds tenant-scoped durable records; existing journal/source history remains immutable.

## Reviewed suggestions

Document identities are explicitly reviewed supplier/date/currency/total values, separate from immutable originals and raw extraction. Normalize supplier text with Unicode NFKC, case folding and punctuation/whitespace normalization. Match exact reviewed date, currency and decimal total. Return same-file and business-identity candidates with reasons, never an automatic merge. Unsupported identity remains an explicit abstention. Each document permits 100 immutable identity revisions; the local business profile permits 200 originals.

Two distinct transactions whose latest reviewed corrections map the same normalized descriptor to the same active income/expense account can suggest a draft rule. Preserve raw descriptors, expose actual correction IDs and provide a source substring for preview. Existing preview/approval/pause/rollback remains the only activation path. High-risk/control accounts are not proposed for rules, and there is no confidence or automatic activation claim.

## Durable resumable exports

Build a bounded journal CSV or evidence ZIP under the existing business lock and retain the exact encrypted bytes in PostgreSQL for 24 hours. This is a ready snapshot, not a pretend background job. Record business version, size, SHA-256 and member ownership. Snapshot creation has a scoped idempotency key; its guarantee lasts for artifact retention. After expiration a new snapshot may be created.

Every metadata/download request rechecks the active session and business membership. RLS also restricts artifacts to their creator. Resume using a single explicit HTTP byte range and an `If-Match` ETag. Each range is at most 1 MiB; the complete artifact is at most 10 MiB. At most three live snapshots are retained per member/business. Expired owned artifacts may be removed on a subsequent successful creation; live artifacts cannot be edited/deleted. These are separate storage caps from original evidence, not a production throughput guarantee. A larger dataset is exported in narrower date windows; unbounded exports are outside the local profile.

## Opening corrections and cash policy

An MFA-authenticated accountant can append a dated, reasoned correction to an opening source. Preserve the original journal, opening amount and reviewed metadata. Require a date on/after posted activity. Increases create additional opening trade sources or principal movements; decreases create linked credit notes or decrease principal. Loan principal cannot become negative; trade credits cannot exceed the source's uncorrected amount. A credit against paid trade balances may leave a customer/vendor credit awaiting the ordinary credited-payment refund workflow. Canonical operation fingerprints include the target opening ID, preventing a retry key from crossing sources.

Cash reporting remains unavailable until every relevant opening trade balance has an immutable evidence-backed policy. The accountant explicitly chooses recognition on settlement with exact income/expense weights, or confirms that the amount was previously recognized. They must also confirm that there is no tax component or that tax was separately accounted for. No tax allocation or historical treatment is inferred. Settlement recognition uses exact allocation, including credits/refunds and linked opening increases; previously recognized amounts do not generate new profit. Reports retain the approving policy IDs, and exports include correction/policy history. Different or tax-inclusive historical policies require complete supported history or an explicit future policy extension.

## P1 privacy fulfillment

Owners/accountants with MFA may review a scoped request from another user. Record outcome, reason, policy reference, retained categories, optional retention date and legal hold in immutable decision history. Export-ready requests use the requester's own MFA-protected export; retained/follow-up outcomes do not claim deletion. Own-user lists and exports show the latest decision. Trial expiry does not revoke read/export/privacy-request or independent privacy-review access.

This fulfills P1's basic manual privacy lifecycle. P2's automated processor deletion, backup/key expiry and jurisdiction-specific execution remain separate release work. This local implementation neither invents a universal retention period nor represents a review decision as completed erasure. No unrestricted support impersonation is added.

## Contracts and acceptance

All JSON actions have explicit allowlisted Pydantic response models; polymorphic source/import/audit payloads use the JSON-value type rather than arbitrary Python objects. Read models do not reapply input-only display normalization to valid historic output. Binary responses, range headers and common error envelopes are documented. The generated OpenAPI artifact is saved under `docs/contracts/openapi.json`. Real PostgreSQL/API/SMTP/ClamAV/OCR acceptance covers new workflows; Phase 4 independently performs the full verification/Swagger/restore/client-generation gate before frontend feature work.
