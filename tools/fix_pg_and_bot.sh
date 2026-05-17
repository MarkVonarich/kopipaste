#!/usr/bin/env bash
set -euo pipefail

log(){ echo -e "\n==> $*"; }

# Подгружаем окружение (для DATABASE_URL)
[ -f /root/.env ] && set -a && . /root/.env && set +a || true
DBURL="${DATABASE_URL:-}"

log "Check disk space/inodes (на всякий)"
df -h
df -i || true

log "Ensure /run/postgresql dir (owner=postgres:postgres, 2775)"
install -d -o postgres -g postgres -m 2775 /run/postgresql
ls -ld /run/postgresql

log "Show who holds :5432 (если кто-то занят — это важно)"
( command -v ss >/dev/null 2>&1 && ss -ltnp | grep -E ':5432\s' ) || true
( command -v lsof >/dev/null 2>&1 && lsof -i :5432 ) || true

log "Tail PG log (последние 80 строк)"
LOG=/var/log/postgresql/postgresql-14-main.log
[ -f "$LOG" ] && tail -n 80 "$LOG" || echo "(no PG log at $LOG)"

log "Пробуем стартануть кластер как postgres (в обход systemd) — увидим чистую ошибку"
if sudo -u postgres /usr/lib/postgresql/14/bin/pg_ctl -D /var/lib/postgresql/14/main -l "$LOG" start; then
  echo "pg_ctl: started"
else
  echo "pg_ctl: start failed (смотрим лог ниже)"
fi

sleep 1
log "pg_isready"
command -v pg_isready >/dev/null 2>&1 && pg_isready -h 127.0.0.1 -p 5432 || true

log "Ещё раз tail PG log (после старта попытки)"
[ -f "$LOG" ] && tail -n 80 "$LOG" || true

# Если порт занят не postgres'ом — вероятно докер/другая служба
log "Если порт 5432 занят НЕ postgres, покажем контейнеры/юниты"
( command -v docker >/dev/null 2>&1 && docker ps --format 'table {{.ID}}\t{{.Names}}\t{{.Ports}}\t{{.Status}}' ) || true
systemctl --no-pager --type=service | grep -E 'postgres|docker|pg' || true

# Если БД уже откликнулась — тестовый запрос
if [ -n "$DBURL" ]; then
  log "Test psql via DATABASE_URL"
  if psql "$DBURL" -c "select now();" -t -A; then
    echo "psql ok"
  else
    echo "psql failed (но продолжаем диагностику)"
  fi
else
  echo "!! DATABASE_URL пуст. Проверь /root/.env"
fi

# Попробуем теперь через systemd (если вручную стартануло, эта команда просто увидит online)
log "systemctl start postgresql@14-main && status"
systemctl start postgresql@14-main.service || true
systemctl --no-pager status postgresql@14-main.service | sed -n '1,40p'

# Если 5432 занят чужим процессом — не перезапускаем бота, чтобы не спамил лог.
# Иначе перезапускаем бота.
if ss -ltnp | grep -E ':5432\s' | grep -v postgres >/dev/null 2>&1; then
  echo "⚠️ Порт 5432 занят НЕ postgres. Не трогаю bot. Разберись, кто слушает (см. вывод выше)."
else
  log "Restart finuchet"
  systemctl restart finuchet
  sleep 1
  systemctl --no-pager status finuchet | sed -n '1,30p'
  log "Last 60 lines of finuchet journal"
  journalctl -u finuchet -n 60 --no-pager
fi
