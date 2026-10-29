# ADR 011 Business sources and local document extraction

Date: 29 October 2026. Status: accepted for the local P1 implementation within the user's approved free scanning/OCR scope.

## Decision and compatibility

Keep the FastAPI modular monolith, PostgreSQL financial authority, forced tenant RLS, canonical posting gateway and future PostgreSQL-outbox/SQS production path. Shared trade sources implement sales and purchasing allocations once. Posted source journals carry immutable operation provenance; editing or reversing the GL independently must not invalidate their subledgers.

The trusted backend dispatcher claims a tenant/actor-scoped PostgreSQL job, commits the lease, then performs scanning and extraction outside a database transaction. Lease validation and active-role checks gate durable completion. Three attempts, bounded exponential delay and expired-lease failure handling preserve recovery visibility. This is the bounded local delivery profile, not a replacement broker or a production SQS implementation.

The user approved local scanning and OCR. ClamAV scans original bytes first. A separate CPU parser container receives only bounded document bytes and its own service token. It holds no database, session-signing, MFA or document-encryption credentials. It runs as a non-root user with a read-only root filesystem, bounded temporary storage, CPU/memory/PID limits, one concurrent extraction and execution deadlines.

Embedded PDF text precedes OCR. The first local OCR adapter uses Tesseract's English model and Poppler; the SRS's PaddleOCR/vision candidates remain later evaluated adapters rather than being represented as already implemented. This implementation choice changes the local extraction adapter, not the system topology or financial contract. The supported matrix and limitations are explicit in [the extraction contract](../contracts/document-extraction.md).

## Review and evidence

Fields include page, normalized region, exact amount/date/text value, engine/model fingerprint and raw OCR score where available. A raw score is not a calibrated probability. Embedded PDF text has no invented confidence value. Semantic roles stay unassigned until human review; extracted text cannot call SQL, tools or posting APIs. Unsupported inputs and unavailable OCR preserve a scanned manual workflow. A failed malware scan leaves the original quarantined.

Private originals use generated object keys and a dedicated encryption key. Text extraction is separate from append-only human notes. Signed export manifests use a distinct Ed25519 key. Free local profiles enforce file, page, word, storage, operation, request and export caps; none establish ten-million-user capacity.

## Consistency and connection budget

Business GET transactions acquire a shared business-row lock; canonical financial writers acquire an exclusive lock. This bounded local policy prevents a multi-query report from combining different financial versions. Keep transactions short: scanning/OCR and external delivery run outside them. The default process budget is ten application connections (five retained plus five overflow) and two independently committed rate-counter connections. A separate counter pool prevents nested admission checks from starving an exhausted application pool. Multiply this budget by process count before scaling; it is not a measured production capacity target.

The parser uses a dedicated bridge with a loopback-published port. Docker Desktop's internal-only bridge made that port unreachable from the host-run worker in real testing. The local adapter does not retrieve URLs, and receives no database/authentication/encryption credentials. Network-level outbound denial remains a production deployment requirement; the local bridge does not claim to enforce it. Container CPU, memory, PID, temporary-storage, non-root and read-only-root controls remain enabled.
