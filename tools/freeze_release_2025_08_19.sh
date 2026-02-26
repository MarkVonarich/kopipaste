#!/usr/bin/env bash
set -euo pipefail

cd /root/bot_finuchet

FILES=(
  "utils/parsing.py"
  "services/records.py"
  "routers/messages.py"
  "ui/messages.py"
  "db/queries.py"
)

TS_HUMAN=$(date '+%Y-%m-%d %H:%M:%S')
TAG="release-2025-08-19-final-reply+parsing-fix"

echo "== Freezing $TAG at $TS_HUMAN =="

# 1) Сводная табличка версий/sha8/размера
printf "\n## %s\n" "$TAG" >> STATUS.md
printf "- Time: %s\n" "$TS_HUMAN" >> STATUS.md
printf "- Files:\n" >> STATUS.md
for f in "${FILES[@]}"; do
  [[ -f "$f" ]] || continue
  ver=$(awk -F'"' '/__version__/{print $2; exit}' "$f" 2>/dev/null || true)
  [[ -n "$ver" ]] || ver="(no __version__)"
  sha=$(sha256sum "$f" | cut -c1-8)
  sz=$(stat -c%s "$f")
  printf "  - %s — v%s — sha8:%s — %s bytes\n" "$f" "$ver" "$sha" "$sz" >> STATUS.md
done

# 2) Короткие release notes
cat >> STATUS.md <<'MD'

### Что вошло
- Новый «итоговый» ответ при записи: *Имя* потратил(а) **СУММА CUR** на **Категория**, дата (день недели), затем *оригинальный ввод* курсивом.
- Оригинальный ввод пользователя всегда сохраняется и показывается, даже если категория выбрана в меню по кнопке.
- **Сумма** и **категория** выделяются жирным.
- Парсер даты/суммы: исправлен кейс двузначных сумм (`кола 20`, `вода 40 сегодня`), безопасная очистка валютных токенов; удалён ложный парс числового «хвоста» как даты.
MD

# 3) Локальный архив ключевых файлов
ARCH="finuchet-$TAG-$(date +%Y%m%d-%H%M%S).tgz"
tar -czf "$ARCH" ${FILES[@]} STATUS.md STATE.yml 2>/dev/null || tar -czf "$ARCH" ${FILES[@]} STATUS.md STATE.yml
echo "Backup archive: $ARCH"

# 4) Встроенный снапшот через systemd (если настроен)
if systemctl list-units --type=service | grep -q 'finuchet-snapshot.service'; then
  systemctl start finuchet-snapshot.service
  sleep 1
  echo "== Snapshot journal =="
  journalctl -u finuchet-snapshot.service -n 20 --no-pager || true
else
  echo "NOTE: finuchet-snapshot.service не найден; пропускаю системный снапшот."
fi

echo "== Tail STATUS.md =="
tail -n 40 STATUS.md
