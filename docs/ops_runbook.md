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
