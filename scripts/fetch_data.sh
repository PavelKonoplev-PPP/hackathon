#!/usr/bin/env bash
# Скачивает датасеты кейса в папку Исходники/ в корне репозитория.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TARGET="$ROOT/Исходники"
URL="https://gitverse.ru/kban/HK_Datasets"

if ls "$TARGET"/*.csv >/dev/null 2>&1; then
  echo "Данные уже скачаны: $TARGET"
  exit 0
fi

for attempt in 1 2 3; do
  echo "Попытка $attempt: скачиваю данные (около 1,4 ГБ)..."
  rm -rf "$TARGET"
  if git -c http.version=HTTP/1.1 clone --depth 1 "$URL" "$TARGET"; then
    rm -rf "$TARGET/.git"
    echo "Готово: $TARGET"
    exit 0
  fi
done

echo "Не удалось скачать данные. Попробуйте позже или скачайте архив со страницы $URL"
exit 1
