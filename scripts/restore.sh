#!/usr/bin/env bash
# ════════════════════════════════════════════════════════════════
# استعادة نسخة scripts/backup.sh — تستبدل البيانات الحالية بالكامل:
#
#   ./scripts/restore.sh ./backups/moj-20261110-030000
#
# يحتاج أن تكون الحاويات أُنشئت مرة (docker compose up -d) حتى توجد الأحجام.
# ════════════════════════════════════════════════════════════════
set -euo pipefail
cd "$(dirname "$0")/.."

SRC="${1:?حدّد مجلد النسخة: ./scripts/restore.sh ./backups/moj-...}"
SRC=$(cd "$SRC" && pwd)
MOUNT="$SRC"
# Git Bash على ويندوز يحوّل مسارات الحاوية (/backup) لمسارات ويندوز؛ لا أثر على لينكس
if command -v cygpath >/dev/null 2>&1; then export MSYS_NO_PATHCONV=1; MOUNT=$(cygpath -m "$SRC"); fi
[ -f "$SRC/backend.tgz" ] || { echo "❌ لا يوجد $SRC/backend.tgz"; exit 1; }

BACKEND=$(docker compose ps -aq backend)
[ -n "$BACKEND" ] || { echo "❌ حاوية backend غير موجودة — شغّل docker compose up -d مرة أولاً"; exit 1; }

read -r -p "⚠️ ستُستبدل كل بيانات موج الحالية بهذه النسخة. اكتب نعم للمتابعة: " ok
[ "$ok" = "نعم" ] || [ "$ok" = "yes" ] || { echo "أُلغيت الاستعادة"; exit 1; }

echo "▶ إيقاف الخادم الخلفي…"
docker compose stop backend >/dev/null

echo "▶ استعادة أحجام الخادم الخلفي…"
docker run --rm --volumes-from "$BACKEND" -v "$MOUNT":/backup:ro alpine:3.20 sh -c '
  set -e
  for d in /app/data /app/app/x/cookies /app/static/uploads /app/app/identity/static/generated; do
    find "$d" -mindepth 1 -delete
  done
  tar xzf /backup/backend.tgz -C /
  # النسخة المتّسقة تحلّ محل الملف الذي نُسخ أثناء التشغيل
  if [ -f /app/data/app.db.backup ]; then
    mv /app/data/app.db.backup /app/data/app.db
    rm -f /app/data/app.db-wal /app/data/app.db-shm /app/data/app.db-journal
  fi
  chown -R 1000:1000 /app/data /app/app/x/cookies /app/static/uploads /app/app/identity/static/generated'

if [ -f "$SRC/neo4j.tgz" ]; then
  NEO4J=$(docker compose ps -aq neo4j)
  echo "▶ استعادة ذاكرة الرسم…"
  docker compose stop neo4j >/dev/null
  docker run --rm --volumes-from "$NEO4J" -v "$MOUNT":/backup:ro alpine:3.20 sh -c '
    set -e; find /data -mindepth 1 -delete; tar xzf /backup/neo4j.tgz -C /'
  docker compose start neo4j >/dev/null
fi

echo "▶ تشغيل الخادم الخلفي…"
docker compose start backend >/dev/null
echo "✅ اكتملت الاستعادة. تابع: docker compose ps   و   docker compose logs -f backend"
