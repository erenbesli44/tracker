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

## Operator: LLM token usage and reasoning effort

Every MiniMax call logs one line, e.g.
`MiniMax usage purpose=analysis model=MiniMax-M3.1-Flash effort=medium prompt_tokens=14843
cached_tokens=128 completion_tokens=6410 reasoning_tokens=2803 seconds=30.4 attempt=1/4`.
`GET /jobs/youtube-watch/health` → `llm_usage_since_start` sums the same counters per purpose
(`analysis`, `outlook`, `quote_clean`, `participants`, `market_inference`, …) since the process
started; they reset on redeploy. Reasoning tokens are billed as output tokens.

MiniMax-M3.1-Flash always thinks and uses `reasoning_effort=max` when the field is omitted; measured
on 2026-11-01, reasoning was 85–90 % of a call's tokens at max and `medium` kept the same topics
at about a third of the cost and a quarter of the latency. The depth is set per call type:

| Env | Default | Call |
|---|---|---|
| `MINIMAX_EFFORT_ANALYSIS` | `medium` | summary + topics of a new video (also `fill-missing-topics`) |
| `MINIMAX_EFFORT_OUTLOOK` | empty (= model default, max) | per-instrument view; lower it only after `scripts/eval_outlook_prompts.py` shows no precision loss on ≥ 50 labelled views |
| `MINIMAX_EFFORT_QUOTE_CLEAN` | `medium` | readable quote |
| `MINIMAX_EFFORT_PARTICIPANTS` | `medium` | multi-guest participants |
| `MINIMAX_EFFORT_INFERENCE` | `medium` | daily market inference |
| `MINIMAX_EFFORT_DEFAULT` | `medium` | legacy summary / classification / economic-thesis prompts |

Values: `low`, `medium`, `high`, `xhigh`, `max`, or empty. Do not use `low`: it dropped 40 % of the
key levels and let a typo into a cleaned quote. An invalid value is logged and ignored. If the
configured model rejects the field (HTTP 400 mentioning reasoning effort), the call is resent
once without it and a warning is logged.

### Transcript time markers (sparse form is opt-in)

The analysis prompt gets the transcript with time markers. Default (`ANALYSIS_TIMESTAMP_EVERY_SECONDS=0`):
one line per caption segment, `[MM:SS-MM:SS] text`. With a positive value: one line per block,
`[MM:SS] text …`, one marker per that many seconds. On a 22-min video (518 segments) the sparse
form at 30 s has 46 % fewer transcript tokens (MiniMax prompt 14.8k → 10.0k) and the call ran in
~30 s instead of ~45 s.

It is **not** the default because recall of borderline topics looked worse on the one video
measured (2026-11-01, `reasoning_effort=medium`, 3 runs per form): per-segment markers found
8/8/8 subtopics (key levels 78/89/77); sparse 30 s found 7/7/6 (86/67/72) and missed the
Dolar/TL topic twice; a start-only marker on every segment found 6/8/8 (77/77/82). The saving is
about $0.0007 per video, so the default stays until a wider eval (≥ 20 videos, both forms, same
effort) shows the same topic sets.
