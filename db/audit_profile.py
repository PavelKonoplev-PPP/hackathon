#!/usr/bin/env python3
"""Проверка всех предполагаемых чисел и дат из профиля на полной PostgreSQL.

Читает JSON профилировщика; не меняет таблицы и не выводит значения строк.
"""
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import psycopg

try:
    from .profile_db import connect
    from .safe_cast import CAST_TYPES, audit_column
except ImportError:
    from profile_db import connect
    from safe_cast import CAST_TYPES, audit_column


def collect_candidates(profile: dict, table_filter: str | None = None) -> list[dict]:
    """Собрать только типы, поддерживаемые безопасным CAST."""
    schema = profile.get("schema")
    if not isinstance(schema, str) or not schema or schema == "information_schema" or schema.startswith("pg_"):
        raise ValueError("Неверная или системная схема профиля")
    tables = profile.get("tables")
    if not isinstance(tables, list):
        raise ValueError("В профиле отсутствует список таблиц")

    candidates = []
    matched = table_filter is None
    seen = set()
    for table in tables:
        name = table.get("name") if isinstance(table, dict) else None
        if not isinstance(name, str) or not name:
            raise ValueError("Неверное имя таблицы в профиле")
        if table_filter is not None and name != table_filter:
            continue
        matched = True
        columns = table.get("columns")
        if not isinstance(columns, list):
            raise ValueError(f"В таблице {name} отсутствуют колонки")
        for column in columns:
            if not isinstance(column, dict):
                raise ValueError(f"Неверный формат колонки в {name}")
            kind = column.get("inferred_type")
            if kind not in CAST_TYPES:
                continue
            column_name = column.get("name")
            if not isinstance(column_name, str) or not column_name:
                raise ValueError(f"Неверное имя колонки в {name}")
            key = (name, column_name)
            if key in seen:
                raise ValueError(f"Дублирующаяся колонка {name}.{column_name}")
            seen.add(key)
            candidates.append({
                "schema": schema,
                "table": name,
                "column": column_name,
                "kind": kind,
                "sample_confidence": column.get("confidence"),
            })
    if not matched:
        raise ValueError(f"Таблица {table_filter} отсутствует в профиле")
    return candidates


def audit_profile(conn, profile: dict, *, table_filter: str | None = None,
                  limit: int | None = None, progress=None) -> dict:
    """Проверяет кандидатов по полным колонкам, продолжая после SQL-ошибки."""
    candidates = collect_candidates(profile, table_filter)
    if limit is not None:
        if limit < 1:
            raise ValueError("limit должен быть положительным")
        candidates = candidates[:limit]

    results = []
    for n, candidate in enumerate(candidates, start=1):
        if progress is not None:
            progress(f"[{n}/{len(candidates)}] {candidate['table']}: {candidate['column']} ({candidate['kind']})")
        try:
            audited = audit_column(
                conn, candidate["schema"], candidate["table"],
                candidate["column"], candidate["kind"],
            )
            audited["status"] = "checked"
            audited["sample_confidence"] = candidate["sample_confidence"]
            # Есть ошибки формата — такой CAST нельзя молча применять в аналитике.
            audited["has_invalid_values"] = audited["invalid_rows"] > 0
            audited["has_missing_values"] = audited["missing_rows"] > 0
            results.append(audited)
        except (psycopg.Error, ValueError) as exc:
            results.append({
                **candidate,
                "status": "error",
                "error": str(exc)[:300],
            })

    checked = [item for item in results if item["status"] == "checked"]
    return {
        "schema": profile["schema"],
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_profile_generated_at_utc": profile.get("generated_at_utc"),
        "statistics_are_full_column": True,
        "source_modified": False,
        "summary": {
            "candidate_columns": len(candidates),
            "checked_columns": len(checked),
            "failed_columns": len(results) - len(checked),
            "columns_with_invalid_values": sum(x["has_invalid_values"] for x in checked),
            "columns_with_missing_values": sum(x["has_missing_values"] for x in checked),
            "fully_convertible_columns": sum(
                x["invalid_rows"] == 0 and x["missing_rows"] == 0 for x in checked
            ),
        },
        "columns": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Проверить типы из JSON-профиля на полных таблицах без их изменения"
    )
    parser.add_argument("--input", type=Path, default=Path("data/all-profile.json"))
    parser.add_argument("--output", type=Path, default=Path("data/type-audit.json"))
    parser.add_argument("--table", help="Сначала проверить только одну таблицу")
    parser.add_argument("--limit", type=int, help="Максимум проверяемых колонок")
    args = parser.parse_args()
    try:
        profile = json.loads(args.input.read_text(encoding="utf-8"))
        if not isinstance(profile, dict):
            raise ValueError("JSON профиля должен быть объектом")
        with connect() as conn:
            report = audit_profile(
                conn, profile, table_filter=args.table, limit=args.limit,
                progress=lambda msg: print(msg, file=sys.stderr, flush=True),
            )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
        print(f"Отчёт: {args.output}")
        return 1 if report["summary"]["failed_columns"] else 0
    except (ValueError, OSError, json.JSONDecodeError, psycopg.Error) as exc:
        print(f"Ошибка аудита: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
