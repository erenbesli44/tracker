# Jobs Module

This directory contains scheduled/background job code that runs outside HTTP request handlers.

Current planned job:

- `youtube_watch/`: Poll tracked YouTube channels, detect new videos, and trigger ingestion pipeline.
- `broker_watch/`: Follow broker model-portfolio change notes (İş, Yapı Kredi, Gedik), store documents, events and composition snapshots (OI-027). Honours robots.txt per host, runs at most `max_runs_per_day` times per UTC day, and flags added/removed events that disagree with the broker's next composition snapshot (`needs_review`). robots.txt: missing (4xx) allows, unreachable or 5xx skips that host for the run (RFC 9309; the owner may relax this).

Design rules:

1. Keep orchestration logic in jobs modules, not inside API routers.
2. Reuse service-layer functions from existing app modules.
3. Keep jobs idempotent and retry-safe.
4. Never hardcode credentials; read from environment variables only.

