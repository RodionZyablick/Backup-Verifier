#!/usr/bin/env bash
# Полный демо-сценарий: источник → бэкап → проверка → намеренная порча → обнаружение.
set -euo pipefail
cd "$(dirname "$0")/.."
PY="${PYTHON:-python3}"
CFG=demo/config.toml

echo "▶ 1/4 Поднимаю демо-БД"
docker compose -f demo/docker-compose.yml up -d
until [ "$(docker inspect -f '{{.State.Health.Status}}' dr_demo_source)" = "healthy" ]; do sleep 1; done

echo "▶ 2/4 Бэкап + автоматическая проверка восстановлением"
$PY -m verifier -c "$CFG" run

echo "▶ 3/4 Портим бэкап (портим байты в середине файла) и проверяем без контрольной суммы"
BAD=$(ls -t backups/*.dump | head -1)
cp "$BAD" "${BAD%.dump}-corrupted.dump"
cp "$BAD.manifest.json" "${BAD%.dump}-corrupted.dump.manifest.json"
printf '\xff\xff\xff\xff' | dd of="${BAD%.dump}-corrupted.dump" bs=1 seek=20000 conv=notrunc 2>/dev/null
$PY -m verifier -c "$CFG" verify --backup "${BAD%.dump}-corrupted.dump" --skip-checksum || echo "   (ожидаемо: код выхода $?)"

echo "▶ 4/4 Та же порча, но с контрольной суммой — ловится ещё до запуска контейнера"
$PY -m verifier -c "$CFG" verify --backup "${BAD%.dump}-corrupted.dump" || echo "   (ожидаемо: код выхода $?)"

echo "▶ Остались ли тестовые контейнеры?"
docker ps -a --filter label=dr-verifier=1 --format '{{.Names}}' | wc -l
docker compose -f demo/docker-compose.yml down -v
