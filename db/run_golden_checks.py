"""BI-06: read-only deterministic golden SQL checks (not an LLM evaluator)."""
import argparse
import json
import os
import re
import sys
from pathlib import Path

import psycopg
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = Path(__file__).with_name("golden_checks.json")
load_dotenv(ROOT / ".env")
ID_RE = re.compile(r"^[a-z][a-z0-9_]*$")


def load_checks(path: Path = MANIFEST) -> list[dict]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("version") != 1:
        raise ValueError("Некорректная версия файла эталонов")
    checks = data.get("checks")
    if not isinstance(checks, list) or not 20 <= len(checks) <= 100:
        raise ValueError("Нужно от 20 до 100 эталонных проверок")
    seen = set()
    for check in checks:
        if not isinstance(check, dict):
            raise ValueError("Каждая проверка должна быть объектом")
        identifier, query = check.get("id"), check.get("sql")
        if not isinstance(identifier, str) or not ID_RE.fullmatch(identifier) or identifier in seen:
            raise ValueError(f"Дублированный или некорректный id проверки: {identifier!r}")
        seen.add(identifier)
        if not isinstance(query, str) or not query.strip().upper().startswith("SELECT "):
            raise ValueError(f"Проверка {identifier}: только SELECT")
        if ";" in query or "--" in query or "/*" in query:
            raise ValueError(f"Проверка {identifier}: запрещены дополнительные команды/комментарии")
        if type(check.get("expected")) not in (int, bool):
            raise ValueError(f"Проверка {identifier}: ожидается integer или boolean")
    return checks


def run_checks(conn, checks: list[dict]) -> dict:
    results = []
    for check in checks:
        try:
            # Always enforce read-only transaction at the DB level.
            with conn.transaction():
                conn.execute("SET TRANSACTION READ ONLY")
                conn.execute("SET LOCAL statement_timeout = '20000ms'")
                with conn.cursor() as cursor:
                    cursor.execute(check["sql"])
                    row = cursor.fetchone()
                    if row is None or len(row) != 1 or cursor.fetchone() is not None:
                        raise ValueError("Ожидалась одна строка и одна колонка")
                    actual = row[0]
            passed = type(actual) is type(check["expected"]) and actual == check["expected"]
            results.append({
                "id": check["id"], "status": "pass" if passed else "fail",
                "expected": check["expected"], "actual": actual,
            })
        except (psycopg.Error, ValueError) as exc:
            results.append({
                "id": check["id"], "status": "error",
                "expected": check["expected"], "error": str(exc).splitlines()[0][:300],
            })
    counts = {key: sum(x["status"] == key for x in results) for key in ("pass", "fail", "error")}
    return {"total": len(results), "passed": counts["pass"],
            "failed": counts["fail"], "errors": counts["error"], "results": results}


def connect_readonly():
    username = os.environ.get("POSTGRES_READER_USER", "bi_reader")
    if username != "bi_reader":
        raise ValueError("Golden-проверки допускаются только под bi_reader")
    password = os.environ.get("POSTGRES_READER_PASSWORD")
    if not password:
        raise ValueError("В .env необходим отдельный POSTGRES_READER_PASSWORD для bi_reader")
    return psycopg.connect(
        host=os.environ.get("POSTGRES_HOST", "127.0.0.1"),
        port=int(os.environ.get("POSTGRES_PORT", "5432")),
        dbname=os.environ.get("POSTGRES_DB", "hackathon"),
        user=username, password=password, connect_timeout=10,
        autocommit=True,
        options="-c default_transaction_read_only=on -c statement_timeout=20000",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Сверить эталонные аналитические SQL с PostgreSQL")
    parser.add_argument("--manifest", type=Path, default=MANIFEST)
    parser.add_argument("--report", type=Path, help="Записать JSON-отчёт")
    args = parser.parse_args()
    try:
        checks = load_checks(args.manifest)
        with connect_readonly() as conn:
            report = run_checks(conn, checks)
        for item in report["results"]:
            suffix = "" if item["status"] == "pass" else (
                f" expected={item['expected']} actual={item.get('actual', item.get('error'))}")
            print(f"{item['status'].upper():5} {item['id']}{suffix}")
        print(f"Итого: {report['passed']}/{report['total']} OK, "
              f"{report['failed']} несовпадений, {report['errors']} ошибок")
        if args.report:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        return 0 if report["passed"] == report["total"] else 1
    except (OSError, ValueError, json.JSONDecodeError, psycopg.Error) as exc:
        print(f"Ошибка BI-06: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
