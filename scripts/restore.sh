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

[ -s "$SRC/postgres.dump" ] || { echo "❌ يلزم postgres.dump. استخدم ترحيل SQLite للنسخ القديمة."; exit 1; }
read -r -p "⚠️ ستُستبدل كل بيانات موج الحالية بهذه النسخة. اكتب نعم للمتابعة: " ok
ok=${ok%$'\r'}
[ "$ok" = "نعم" ] || [ "$ok" = "yes" ] || { echo "أُلغيت الاستعادة"; exit 1; }

docker compose up -d postgres >/dev/null
ready=0
for _ in $(seq 1 30); do
  if docker compose exec -T postgres sh -c 'pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB"' >/dev/null 2>&1; then ready=1; break; fi
  sleep 2
done
[ "$ready" = 1 ] || { echo "❌ PostgreSQL غير جاهز"; exit 1; }
catalog=$(docker compose exec -T postgres pg_restore --list < "$SRC/postgres.dump")
for table in 'xsuite scheduled_posts' 'timeline x_timeline_tweets' 'agent_security audit'; do
  grep -Fq " TABLE $table " <<< "$catalog" || { echo "❌ نسخة PostgreSQL قديمة أو ناقصة؛ راجع إجراء الترحيل"; exit 1; }
done

echo "▶ إيقاف الخادم الخلفي…"
docker compose stop backend >/dev/null

echo "▶ استعادة أحجام الخادم الخلفي…"
docker run --rm --volumes-from "$BACKEND" -v "$MOUNT":/backup:ro alpine:3.20 sh -c '
  set -e
  for d in /app/data /app/app/x/cookies /app/static/uploads /app/app/identity/static/generated; do
    find "$d" -mindepth 1 -delete
  done
  tar xzf /backup/backend.tgz -C /
  chown -R 1000:1000 /app/data /app/app/x/cookies /app/static/uploads /app/app/identity/static/generated'

docker compose exec -T postgres sh -c 'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists --no-owner --single-transaction'     < "$SRC/postgres.dump"

if [ -f "$SRC/neo4j.tgz" ]; then
  NEO4J=$(docker compose ps -aq neo4j)
  echo "▶ استعادة ذاكرة الرسم…"
  docker compose stop neo4j >/dev/null
  docker run --rm --volumes-from "$NEO4J" -v "$MOUNT":/backup:ro alpine:3.20 sh -c '
    set -e; find /data -mindepth 1 -delete; tar xzf /backup/neo4j.tgz -C /'
  docker compose start neo4j >/dev/null
fi

echo "▶ تشغيل الخادم الخلفي…"
docker compose run --rm --no-deps -T backend python -c "
from app.aeos.security import ledger
with ledger.transaction() as db:
    ledger.verify_chain(db)
    db.execute('DELETE FROM grants')
    ledger.audit(db, 'restore', None, 'approvals', 'invalidated')"
docker compose start backend >/dev/null
echo "✅ اكتملت الاستعادة. تابع: docker compose ps   و   docker compose logs -f backend"
