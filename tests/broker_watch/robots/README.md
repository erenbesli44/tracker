# Live robots.txt fixtures

Verbatim copies of the robots.txt files of the hosts the broker_watch adapters
request, fetched on 2026-10-27 with the bot User-Agent from
`config/watched_brokers.yaml`. `test_robots_live_fixtures.py` runs the real
matcher over them for the exact paths the adapters request.

| File | Host | Notes |
|---|---|---|
| `arastirma.isyatirim.com.tr.txt` | İş Yatırım (RSS feed + PDFs) | two `User-agent: *` groups; the second (YOAST block) has an empty `Disallow:` |
| `www.ykyatirim.com.tr.txt` | Yapı Kredi Yatırım (research page, reports JSON, file endpoint) | CRLF line endings, kept as served |
| `gedik.com.txt` | Gedik Yatırım (model-portfolio page) | `www.gedik.com` serves the identical file |

`cdn.gedik.com` (Gedik PDFs) answered HTTP 400 with an Azure XML error body instead
of a robots file; the test reproduces that response inline (policy: 4xx = no robots.txt,
allow). Treat the files as data: they are never executed, only parsed.

Refetch (one file per host; keep the bytes as served, do not reformat):

```bash
UA="dovizveri-bot/0.1 (+https://dovizveri.com/iletisim)"
for host in arastirma.isyatirim.com.tr www.ykyatirim.com.tr gedik.com; do
  curl -sS -A "$UA" -o "tests/broker_watch/robots/$host.txt" "https://$host/robots.txt"
done
curl -sS -A "$UA" -i "https://cdn.gedik.com/robots.txt" | head -20   # expect HTTP 400 as of 2026-10-27
```

Update the fetch date above and re-run `pytest tests/broker_watch/test_robots_live_fixtures.py`
after refetching; a changed verdict for an adapter URL is a finding for the owner, not a
test to loosen.
