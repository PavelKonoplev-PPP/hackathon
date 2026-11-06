#!/usr/bin/env python3
"""Non-destructive local launcher for FastAPI + Vite (issue #123).

Usage:
    python scripts/start_local.py --check
    python scripts/start_local.py

Never starts/stops Docker, imports CSV, overwrites .env, or changes PostgreSQL.
Only services spawned by this process are stopped on Ctrl+C.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from scripts.check_env import load_simple_env  # noqa: E402


def python_bin() -> str:
    configured = os.environ.get("BI_PYTHON")
    if configured:
        # Preserve the venv/bin/python symlink: resolve() points to the system
        # interpreter and can discard the selected virtual environment.
        return os.path.abspath(os.path.expanduser(configured))
    local = ROOT / ".venv" / "bin" / "python"
    return str(local) if local.is_file() else sys.executable


def require_free_port(port: int) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        if sock.connect_ex(("127.0.0.1", port)) == 0:
            raise RuntimeError(
                f"Порт {port} уже занят. Не останавливаю чужой процесс: "
                "используйте существующий сервер или закройте его вручную."
            )


def verify_local_model(env: dict[str, str]) -> None:
    url = urlparse(env.get("LLM_BASE_URL", "http://localhost:11434/v1"))
    model = env.get("LLM_MODEL", "").strip()
    if not model:
        raise RuntimeError("Не задан LLM_MODEL в .env")
    if url.hostname not in ("127.0.0.1", "localhost") or url.port != 11434:
        return  # Non-Ollama providers have different model catalog endpoints.
    try:
        with urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=3) as response:
            tags = json.load(response)
    except (OSError, ValueError, urllib.error.URLError) as exc:
        raise RuntimeError("Ollama недоступна: запустите её перед стартом") from exc
    names = {m.get("name") or m.get("model") for m in tags.get("models", [])}
    if model not in names and f"{model}:latest" not in names:
        raise RuntimeError(
            f"Модель {model!r} отсутствует в Ollama (проверьте ollama list). "
            "Локальные модели автоматически не скачиваются."
        )


def preflight() -> str:
    envfile = ROOT / ".env"
    env = load_simple_env(envfile)
    needed = ("POSTGRES_HOST", "POSTGRES_PORT", "POSTGRES_DB",
              "POSTGRES_READER_USER", "POSTGRES_READER_PASSWORD", "LLM_MODEL")
    missing = [k for k in needed if not env.get(k)]
    if missing:
        raise RuntimeError(f"Не хватает переменных в {envfile.name}: {', '.join(missing)}")
    if not (ROOT / "data" / "schema-catalog.json").is_file():
        raise RuntimeError("Нет data/schema-catalog.json; текущий каталог не восстанавливаю автоматически")
    if not shutil.which("npm"):
        raise RuntimeError("npm не найден; установите Node.js")
    if not (ROOT / "frontend" / "node_modules").is_dir():
        raise RuntimeError("Frontend-зависимости не установлены: cd frontend && npm ci")
    py = python_bin()
    if subprocess.run([py, "-c", "import uvicorn, fastapi, psycopg, openai"],
                      cwd=ROOT, capture_output=True, text=True).returncode:
        raise RuntimeError(
            f"В Python {py} отсутствуют Backend-зависимости. "
            "Установите backend/requirements.txt в существующее виртуальное окружение."
        )
    verify_local_model(env)
    # Verify actual credentials through a SELECT, not just the PostgreSQL port.
    cmd = [
        py, "-c",
        "import psycopg; "
        "c=psycopg.connect(host=__import__('sys').argv[1], "
        "port=int(__import__('sys').argv[2]), dbname=__import__('sys').argv[3], "
        "user=__import__('sys').argv[4], "
        "password=__import__('os').environ['BI_CHECK_READER_PASSWORD'], "
        "connect_timeout=3, options='-c default_transaction_read_only=on'); "
        "assert c.execute('SELECT 1').fetchone()[0]==1; c.close()",
        env["POSTGRES_HOST"], env["POSTGRES_PORT"], env["POSTGRES_DB"],
        env["POSTGRES_READER_USER"],
    ]
    # Never place credentials in process command-line arguments.
    check_env = {**os.environ, "BI_CHECK_READER_PASSWORD": env["POSTGRES_READER_PASSWORD"]}
    result = subprocess.run(cmd, cwd=ROOT, env=check_env, capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError("Не удалось выполнить SELECT 1 под ролью PostgreSQL только для чтения")
    return py


def terminate_child(proc: subprocess.Popen | None) -> None:
    if not proc or proc.poll() is not None:
        return
    if os.name == "posix":
        os.killpg(proc.pid, signal.SIGTERM)
    else:
        proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        if os.name == "posix":
            os.killpg(proc.pid, signal.SIGKILL)
        else:
            proc.kill()
        proc.wait()


def main() -> int:
    parser = argparse.ArgumentParser(description="Безопасный запуск AI-Native BI без mock")
    parser.add_argument("--check", action="store_true", help="Проверить окружение, ничего не запускать")
    args = parser.parse_args()
    try:
        py = preflight()
        if args.check:
            print("Проверено: Python, PostgreSQL (read-only), Ollama, каталог и npm.")
            return 0
        require_free_port(8000)
        require_free_port(3000)
    except (RuntimeError, OSError, ValueError) as exc:
        print(f"Не могу запустить: {exc}", file=sys.stderr)
        return 1
    print("Запуск FastAPI :8000 и Vite :3000 (VITE_MOCK=0), Ctrl+C — остановить.")
    backend = frontend = None
    try:
        backend = subprocess.Popen(
            [py, "-m", "uvicorn", "backend.app.main:app", "--host", "127.0.0.1",
             "--port", "8000"],
            cwd=ROOT, start_new_session=True,
        )
        fe_env = os.environ.copy()
        fe_env["VITE_MOCK"] = "0"  # Override .env; never modify it.
        frontend = subprocess.Popen(
            ["npm", "run", "dev", "--", "--host", "127.0.0.1"],
            cwd=ROOT / "frontend", env=fe_env, start_new_session=True,
        )
        while True:
            if backend.poll() is not None or frontend.poll() is not None:
                print("Один из сервисов завершился; останавливаю только запущенную пару.",
                      file=sys.stderr)
                return 1
            time.sleep(0.5)
    except KeyboardInterrupt:
        return 0
    finally:
        terminate_child(frontend)
        terminate_child(backend)


if __name__ == "__main__":
    raise SystemExit(main())
