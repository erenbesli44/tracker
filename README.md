# tracker

## Operator: fill topics missed on recent videos

Until 2026-10-29 the analysis call saw only ~12k characters of each transcript
(head/middle/tail), so topics discussed elsewhere in a video got no mention and
no outlook. New videos are now analysed on the full transcript up to
`ANALYSIS_TRANSCRIPT_MAX_CHARS` (default 80,000 characters, about a 60 min video;
tunable via env). Longer transcripts (roughly > 60-70 min) are still cut to
head/middle/tail at that size.

`POST /ingestions/fill-missing-topics?days=14&limit=50&workers=2&dry_run=true`
(`X-API-Key` required) re-runs the analysis on the full transcript of recent
videos that already have mentions and adds a mention only for topics a video does
not have yet, then classifies the outlook of those new mentions only. Existing
mentions and summaries are never changed. `GET /ingestions/fill-missing-topics`
shows progress: `scanned`, `videos_with_new_topics`, `new_mentions_by_topic`,
`videos` (`{video_id: [{topic, confidence}]}`), `failed`, `last_error`.

- `dry_run` defaults to `true` and writes nothing. It is an estimate: the real run
  calls the LLM again and may return a slightly different set of topics.
- Start with a small `limit` (e.g. 5), check the result, then widen.
- One run at a time; a second POST returns `already_running`.
- The run lives in the API process: do not redeploy while it runs (it would stop
  half-way; rerunning is safe because only missing topics are added).
- Each video costs one analysis call (plus one outlook call when instruments are
  added). Watch `failed` / `last_error` for MiniMax errors such as low balance.
