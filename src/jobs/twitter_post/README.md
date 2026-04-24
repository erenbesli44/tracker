# Twitter Post Job

Polls the tracker DB for new `VideoSummary` rows and posts each one as a
single tweet on a bot account. Mirrors the `youtube_watch` job structure.

## How it works

1. Query `video` ⨝ `video_summary` filtered by `freshness_days`.
2. Skip any video that already has a row in `twitter_post` (unique per `video_id`).
3. Build tweet text: trimmed `short_summary` + blank line + video URL (≤280 chars).
4. POST via the X API v2 (`tweepy.Client.create_tweet`).
5. Record one `twitter_post` row per attempt (`posted` / `failed` / `skipped`).
6. Record aggregate metrics on a `twitter_post_run` row.

## Run it

```bash
python -m src.jobs.twitter_post.runner
```

Schedule via cron, systemd timer, Kubernetes CronJob, etc. — same shape as
the `youtube_watch` runner. Recommended cadence: every 10–15 minutes.

Exit codes: `0` success, `1` partial success, `2` fatal / all failed.

## Configuration

All settings come from environment variables (loaded via `pydantic-settings`
from `.env`):

| Var | Purpose |
|---|---|
| `TWITTER_API_KEY` | OAuth 1.0a consumer key |
| `TWITTER_API_SECRET` | OAuth 1.0a consumer secret |
| `TWITTER_ACCESS_TOKEN` | Bot account access token |
| `TWITTER_ACCESS_TOKEN_SECRET` | Bot account access token secret |
| `TWITTER_HANDLE` | Bot handle, used only to build `tweet_url` (optional) |
| `TWITTER_DRY_RUN` | `true` to log tweets instead of posting (default `false`) |
| `TWITTER_MAX_POSTS_PER_RUN` | Hard cap per run (default `5`) |
| `TWITTER_FRESHNESS_DAYS` | Ignore summaries older than this on first run (default `7`) |

Get the four OAuth values from https://developer.x.com → your app → Keys and
Tokens. The bot account must have Read + Write permissions.

### First-run smoke test

```bash
TWITTER_DRY_RUN=true python -m src.jobs.twitter_post.runner
```

Logs each tweet it *would* have posted and creates `twitter_post` rows with
`status='posted'` and `tweet_id='dryrun'`. Useful for verifying the query and
the formatter without burning API quota — but note: videos recorded in dry-run
mode won't be re-posted for real later. To clear them:

```sql
DELETE FROM twitter_post WHERE tweet_id = 'dryrun';
```

## Observability

Read-only endpoints (FastAPI):

- `GET /jobs/twitter-post/runs?limit=20` — recent runs
- `GET /jobs/twitter-post/runs/{run_id}` — one run + its posts
- `GET /jobs/twitter-post/posts?status=failed` — filter by status

## Retrying a failed post

The `twitter_post.video_id` column is unique, so a failed attempt blocks
future retries. To retry, delete the row manually:

```sql
DELETE FROM twitter_post WHERE video_id = <id> AND status = 'failed';
```

The next runner execution will pick the video up again.

## Free tier note

X's free tier allows ~500 writes/month (≈16/day). Keep
`TWITTER_MAX_POSTS_PER_RUN` low and consider filtering candidates (e.g. by
topic) once backlog catches up.
