# Finuchet Ops Runbook (Post-migration)

## 1) Telegram API connectivity check
```bash
curl -I --connect-timeout 8 --max-time 12 https://api.telegram.org/
```
Expected: reachable endpoint (HTTP 302 is acceptable for this probe).

## 2) Service health checks
```bash
systemctl status finuchet --no-pager
journalctl -u finuchet -n 100 --no-pager
```

## 3) Logs since latest service start
```bash
journalctl -u finuchet --since "$(systemctl show -p ActiveEnterTimestamp finuchet | cut -d= -f2- | sed 's/ UTC$//' | sed 's/ MSK$//')" --no-pager
```

## 4) Verify latest DB operations
```bash
sudo -u postgres psql -P pager=off -d finance_bot -c "SELECT id, user_id, op_date, type, category, amount, created_at FROM public.operations ORDER BY id DESC LIMIT 10;"
```

## 5) Dependency sanity check
```bash
.venv/bin/python - <<'PY'
import requests, dateparser, telegram, psycopg2, apscheduler, rapidfuzz
import pandas, sklearn, joblib
import main
print("runtime imports ok")
PY
```

## 6) Safety warning
- Never run old and new servers simultaneously with the same Telegram token.

## 7) Scheduler jobs and feature-flag diagnostics
```bash
SINCE_NO_TZ="$(systemctl show -p ActiveEnterTimestamp finuchet | cut -d= -f2- | sed 's/ UTC$//' | sed 's/ MSK$//')"
journalctl -u finuchet --since "$SINCE_NO_TZ" --no-pager | egrep -i "scheduler flags|Added job|Skipped job|day_nudge|evening_reminder|weekly_report|monthly_report|smart_morning_limit|fx_update|Scheduler started|Application started"
```

## 8) Report/smart-morning dedup and toggle rows
```bash
sudo -u postgres psql -P pager=off -d finance_bot -c "
SELECT user_id, sent_on, kind, tag, sent_at
FROM public.reminders_log
WHERE kind LIKE 'weekly_report:%'
   OR kind LIKE 'monthly_report:%'
   OR kind IN ('smart_morning_limit','smart_morning_opt_in','smart_morning_opt_out')
ORDER BY sent_at DESC
LIMIT 50;
"
```

## 9) Limits state snapshot
```bash
sudo -u postgres psql -P pager=off -d finance_bot -c "
SELECT user_id, period, category, amount, currency, updated_at
FROM public.category_limits
ORDER BY updated_at DESC
LIMIT 50;
"
```

- Для голосового ввода нужен ffmpeg: `apt-get install -y ffmpeg`
