# Million-Page Daily Ingestion & 500,000-Page Backlog Isolation

## 1. Capacity Targets

| Metric | Brief Requirement | LedgerPilot Design Target |
| :--- | :--- | :--- |
| Daily Sustained Document Volume | `1,000,000 pages / day` (`11.57 pages/sec` avg) | `2,000,000 pages / day` (`60 pages/sec` peak burst) |
| Single-Tenant Historical Backlog | `500,000 pages` uploaded at once | `bulk` lane fair-share cap (`max_bulk_inflight_per_tenant = 8`) |
| Foreground Interactive Latency | No noticeable slowdown for other tenants | `p95 < 120 ms` across `/dashboard/executive-summary`, `/reports/*`, and `/public/v1/*` |

## 2. Two-Lane Fair-Share Architecture (`app.ingestion_queue_jobs`)

1. **Dedicated Lane Separation (`live` vs. `bulk`):**
   - Everyday mobile snaps (`/capture`), inbound supplier emails (`/webhooks/inbound-email`), and interactive uploads enter `lane = 'live'`.
   - Historical archive backfills (`/p4/bulk-ingestion/submit`) enter `lane = 'bulk'`.
2. **`FOR UPDATE SKIP LOCKED` + Per-Tenant In-Flight Cap:**
   - `lease_next_fair_share_job()` in `backend/src/ledgerpilot/documents/fair_share_queue.py` always drains `live` jobs ahead of `bulk` jobs and caps concurrent `bulk` leases per `business_id` (`max_bulk_inflight_per_tenant`), preventing any noisy neighbor with a 500k-page archive from starving shared workers or DB connection pools.
3. **Zero-RAM-Buffering Streaming Storage:**
   - `storage.put_stream_async` encrypts in `64 KiB` chunks (`LPENC1` / `LPENV1`) with rolling SHA-256 so memory stays flat under high concurrency.

## 3. Running the Load Suite (`k6`)

```powershell
k6 run scripts/load/live-traffic.js
k6 run scripts/load/ingest-1m-pages.js
k6 run scripts/load/backlog-500k-isolation.js
```
