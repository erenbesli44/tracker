# Jobs Module

This directory contains scheduled/background job code that runs outside HTTP request handlers.

Current planned job:

- `youtube_watch/`: Poll tracked YouTube channels, detect new videos, and trigger ingestion pipeline.

Design rules:

1. Keep orchestration logic in jobs modules, not inside API routers.
2. Reuse service-layer functions from existing app modules.
3. Keep jobs idempotent and retry-safe.
4. Never hardcode credentials; read from environment variables only.

