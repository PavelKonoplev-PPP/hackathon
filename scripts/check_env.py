#!/usr/bin/env python3
"""Pre-flight checker for Hackathon BI platform (Issue #123).

Verifies:
1. Presence and correctness of .env configuration.
2. PostgreSQL database connectivity (port check).
3. Raw datasets in Исходники/ (9 CSV files).
4. Metadata catalogs in data/.
5. LLM API connectivity (Ollama / vLLM / OpenAI-compatible).
6. Frontend mock flag (VITE_MOCK).

Usage:
    python scripts/check_env.py
"""

import os
import socket
import sys
from pathlib import Path
from urllib.parse import urlparse

# Ensure stdout handles UTF-8 on Windows cp1251 consoles
if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = PROJECT_ROOT / ".env"
DATA_DIR = PROJECT_ROOT / "data"
SOURCES_DIR = PROJECT_ROOT / "Исходники"
FRONTEND_ENV = PROJECT_ROOT / "frontend" / ".env"


def load_simple_env(path: Path) -> dict[str, str]:
    env = {}
    if not path.is_file():
        return env
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip("'\"")
    return env


def check_port(host: str, port: int, timeout: float = 2.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def main() -> int:
    print("=" * 60)
    print("  AI-Native BI: Pre-Flight Environment Diagnostic (#123)")
    print("=" * 60)

    issues = []
    warnings = []

    # 1. Check .env
    env = load_simple_env(ENV_PATH)
    if not ENV_PATH.is_file():
        issues.append(
            "Файл .env не найден в корне проекта. "
            "Скопируйте .env.example в .env и укажите свои пароли."
        )
    else:
        print(" [OK] Файл .env найден.")
        required_keys = [
            "POSTGRES_HOST", "POSTGRES_PORT", "POSTGRES_DB",
            "POSTGRES_USER", "POSTGRES_PASSWORD",
            "POSTGRES_READER_USER", "POSTGRES_READER_PASSWORD"
        ]
        missing = [k for k in required_keys if not env.get(k)]
        if missing:
            issues.append(f"В .env отсутствуют обязательные переменные: {', '.join(missing)}")
        if env.get("POSTGRES_PASSWORD") == "change_me_local_only":
            warnings.append("POSTGRES_PASSWORD содержит значение по умолчанию из шаблона.")

    # 2. Check PostgreSQL port & connection
    pg_host = env.get("POSTGRES_HOST", "127.0.0.1")
    pg_port = int(env.get("POSTGRES_PORT", "5432"))
    if check_port(pg_host, pg_port):
        print(f" [OK] Порт PostgreSQL доступен ({pg_host}:{pg_port}).")
    else:
        warnings.append(
            f"PostgreSQL не отвечает на {pg_host}:{pg_port}. "
            f"Убедитесь, что служба PostgreSQL или контейнер Docker запущены."
        )

    # 3. Check Datasets
    if SOURCES_DIR.is_dir():
        csvs = list(SOURCES_DIR.glob("*.csv"))
        if len(csvs) >= 9:
            print(f" [OK] Исходные датасеты найдены: {len(csvs)} CSV файлов в {SOURCES_DIR.name}/.")
        elif csvs:
            warnings.append(f"В {SOURCES_DIR.name}/ найдено только {len(csvs)} из 9 CSV файлов.")
        else:
            warnings.append(f"Папка {SOURCES_DIR.name}/ пуста. Запустите scripts/fetch_data.sh.")
    else:
        warnings.append("Папка Исходники/ отсутствует. Запустите scripts/fetch_data.sh.")

    # 4. Check Catalogs
    catalog_file = DATA_DIR / "schema-catalog.json"
    if catalog_file.is_file():
        print(" [OK] Каталог схемы data/schema-catalog.json найден.")
    else:
        warnings.append(
            "Каталог data/schema-catalog.json не найден. "
            "После загрузки базы выполните python db/catalog_db.py."
        )

    # 5. Check LLM / Ollama
    llm_url = env.get("LLM_BASE_URL", "http://localhost:11434/v1")
    parsed_url = urlparse(llm_url)
    llm_host = parsed_url.hostname or "localhost"
    llm_port = parsed_url.port or (443 if parsed_url.scheme == "https" else 80)
    if check_port(llm_host, llm_port):
        print(f" [OK] LLM сервис доступен на {llm_host}:{llm_port}.")
    else:
        warnings.append(
            f"LLM сервис не отвечает на {llm_host}:{llm_port}. "
            f"Если Ollama не запущена, интерфейс можно тестировать в демо-режиме (VITE_MOCK=1) "
            f"или настроить внешнюю модель в LLM_BASE_URL."
        )

    # 6. Check Frontend configuration
    fe_env = load_simple_env(FRONTEND_ENV)
    vite_mock = fe_env.get("VITE_MOCK", "1")
    if vite_mock == "1":
        print(" [INFO] Фронтенд настроен на демо-режим (VITE_MOCK=1).")
    else:
        print(" [OK] Фронтенд настроен на работу с реальным бэкендом (VITE_MOCK=0).")

    print("-" * 60)
    if issues:
        print("ОШИБКИ (требуют исправления):")
        for err in issues:
            print(f"  [!] {err}")
    if warnings:
        print("ПРЕДУПРЕЖДЕНИЯ:")
        for w in warnings:
            print(f"  [-] {w}")

    if not issues and not warnings:
        print(" Все проверки успешно пройдены! Платформа готова к запуску.")
        return 0
    elif not issues:
        print(" Платформа готова к запуску с предупреждениями.")
        return 0
    else:
        print(" Перед запуском устраните ошибки конфигурации.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
