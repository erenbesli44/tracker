# Jobs Module

This directory contains scheduled/background job code that runs outside HTTP request handlers.

Current planned job:

- `youtube_watch/`: Poll tracked YouTube channels, detect new videos, and trigger ingestion pipeline.
- `broker_watch/`: Follow broker model-portfolio change notes (İş, Yapı Kredi, Gedik), store documents, events and composition snapshots (OI-027). Honours robots.txt per host, runs at most `max_runs_per_day` times per UTC day, and flags added/removed events that disagree with the broker's next composition snapshot (`needs_review`). robots.txt: missing (4xx) allows, unreachable or 5xx skips that host for the run (RFC 9309; the owner may relax this). One-off backfill: `python -m src.jobs.broker_watch.runner --backfill --since YYYY-MM-DD` bypasses the daily limit (the run still counts toward it for the rest of the day), follows the İş feed pages back to that date (hard cap 20 pages; items are filtered on the RSS post date while the stored `published_at` stays the PDF filename date, which can differ), filters Yapı Kredi on `reportPostTime` and Gedik on the PDF filename date, sleeps a 1 s courtesy delay before every request after the first (robots.txt fetches and redirect hops included; `--delay`), and enforces a document cap (`--max-documents`, default 40) that is cumulative across sources and counts documents iterated, already-stored ones included: a source whose discovery would exceed what is left is recorded as failed, nothing of it is fetched or charged, and later sources still run. The run row carries `"backfill since <date>"` first in `error_details`. Production backfill without a shell: `POST /jobs/broker-watch/backfill?since=YYYY-MM-DD[&max_documents=N]` (API key; 202; one run at a time; same run as `--backfill`), progress at `GET /jobs/broker-watch/backfill`. Do not redeploy during a run (an interrupted run stays `running`).
- `broker_watch/public_router.py`: read-only API (API key) — `GET /brokers/` (brokers, `enabled` from config, served-event counts), `GET /brokers/model-portfolio/events?days=90&broker=<slug>&limit=100` (newest first, each with its document URL and the same broker's `previous` event for the ticker), `GET /brokers/{slug}/model-portfolio` (latest composition as ticker + entry date only, last 10 events); only `auto`/`reviewed` events are served, never `needs_review`/`rejected` or document text.

Design rules:

1. Keep orchestration logic in jobs modules, not inside API routers.
2. Reuse service-layer functions from existing app modules.
3. Keep jobs idempotent and retry-safe.
4. Never hardcode credentials; read from environment variables only.

