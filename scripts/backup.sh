#!/usr/bin/env bash
# ════════════════════════════════════════════════════════════════
# نسخة احتياطية لبيانات موج على الخادم (تُشغَّل من مجلد المشروع أو من أي مكان):
#
#   ./scripts/backup.sh                     ← إلى ./backups/moj-<التاريخ>
#   ./scripts/backup.sh /mnt/backups        ← مجلد آخر
#   ./scripts/backup.sh /mnt/backups --with-neo4j   ← + ذاكرة الرسم (يوقف neo4j ثوانٍ)
#
# يُحفظ PostgreSQL عبر pg_dump مع ملفات الموظفين وبيانات الاعتماد ووسائط الحسابات.
# الاستعادة: scripts/restore.sh
# ⚠️ النسخة تحوي كوكيز جلسات X وأسراراً: احفظها في مكان محمي.
# ════════════════════════════════════════════════════════════════
set -euo pipefail
cd "$(dirname "$0")/.."

DEST="${1:-./backups}"
WITH_NEO4J=0
for a in "$@"; do [ "$a" = "--with-neo4j" ] && WITH_NEO4J=1; done
[ "${DEST}" = "--with-neo4j" ] && DEST="./backups"

BACKEND=$(docker compose ps -q backend)
[ -n "$BACKEND" ] || { echo "❌ حاوية backend غير موجودة — شغّل النظام أولاً (docker compose up -d)"; exit 1; }

umask 077
OUT="$DEST/moj-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$OUT"
OUT=$(cd "$OUT" && pwd)
MOUNT="$OUT"
# Git Bash على ويندوز يحوّل مسارات الحاوية (/backup) لمسارات ويندوز؛ لا أثر على لينكس
if command -v cygpath >/dev/null 2>&1; then export MSYS_NO_PATHCONV=1; MOUNT=$(cygpath -m "$OUT"); fi

echo "▶ حفظ PostgreSQL بجميع مخططاته…"
docker compose exec -T postgres sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' > "$OUT/postgres.dump.partial"
test -s "$OUT/postgres.dump.partial"
mv "$OUT/postgres.dump.partial" "$OUT/postgres.dump"

echo "▶ أرشفة أحجام الخادم الخلفي…"
docker run --rm --volumes-from "$BACKEND" -v "$MOUNT":/backup alpine:3.20 \
  tar czf /backup/backend.tgz -C / \
  app/data app/app/x/cookies app/static/uploads app/app/identity/static/generated

if [ "$WITH_NEO4J" = "1" ]; then
  NEO4J=$(docker compose ps -q neo4j)
  echo "▶ ذاكرة الرسم: إيقاف neo4j مؤقتاً للنسخ المتّسق…"
  docker compose stop neo4j >/dev/null
  docker run --rm --volumes-from "$NEO4J" -v "$MOUNT":/backup alpine:3.20 tar czf /backup/neo4j.tgz -C / data
  docker compose start neo4j >/dev/null
fi

cp .env "$OUT/env.backup" 2>/dev/null || true
chmod -R go-rwx "$OUT"
echo "✅ النسخة في: $OUT"
ls -lh "$OUT"
