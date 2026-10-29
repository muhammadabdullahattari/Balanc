# Local P1 document extraction contract

This supplements FR-009 and the optional local inference profile without changing financial posting or the production model roadmap.

Upload PDF, PNG, JPEG, HEIC, UTF-8 CSV or XLSX originals through the authenticated, CSRF-protected evidence API. Originals are capped at 25 MiB and retained encrypted under tenant-generated keys. Upload success means queued/quarantined, not scanned or posted. The durable worker must receive a positive ClamAV result before parsing or download/review access.

The first automatic matrix is embedded text in PDFs, followed by English OCR for scanned PDF, PNG and JPEG. PDF processing is capped at ten pages; images at sixteen megapixels; extraction at twenty thousand words, two hundred thousand text characters and an eighty-five-second parser budget. HEIC, CSV and XLSX currently retain scanned manual review; automatic extraction of these formats is explicitly unsupported. Ambiguous dates, currency identity, supplier identity and invoice-total semantics are not guessed.

`GET /evidence/{id}` returns raw extracted text separately from extraction metadata. Each candidate has a type, string value, page, normalized left/top/right/bottom region and unassigned semantic role. Tesseract confidence is its uncalibrated OCR score; embedded text has null confidence. `model_version` identifies the extraction engine and, for OCR, the installed English model hash. Human notes/field assignments are append-only and cannot overwrite original text. No extraction endpoint posts financial data.

The parser service is loopback/private-network only, authenticates with `OCR_SERVICE_TOKEN`, has no database credentials and performs no URL retrieval. The trusted dispatcher validates responses before a lease-protected database completion. Parser unavailability produces an explicit manual-review outcome; scanner unavailability retries while preserving quarantine. `GET /jobs` exposes bounded job status without lease tokens.

Run local services with `docker compose -f docker-compose.documents.yml up -d --build`. Run a scoped worker from `backend` with `uv run python -m ledgerpilot.documents.worker --actor USER_UUID --business BUSINESS_UUID`. These are private operator parameters, not a public impersonation API. Provider/model promotion, non-English matrices, streaming global dispatch and production SQS delivery require their own acceptance evidence.

Implementation references: [Tesseract TSV output](https://tesseract-ocr.github.io/tessdoc/Command-Line-Usage.html), [ClamAV Docker deployment](https://docs.clamav.net/manual/Installing/Docker.html), and [Poppler PDF rendering](https://manpages.debian.org/experimental/poppler-utils/pdftoppm.1.en.html).
