#!/usr/bin/env python3
"""Полный аудит идентификаторов без устойчивого префикса (read-only).

Проверяет количества уникальных/повторных/пустых ключей, не выводя ID.
Не устанавливает бизнес-связи или разрешения на JOIN.
"""
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import psycopg
from psycopg import sql

try:
    from .profile_db import connect
except ImportError:
    from profile_db import connect


def unprefixed_identifiers(relationships: dict) -> tuple[str, list[dict]]:
    if not isinstance(relationships, dict):
        raise ValueError("Неверный формат отчёта")
    schema = relationships.get("schema")
    if (not isinstance(schema, str) or not schema or
            schema == "information_schema" or schema.startswith("pg_")):
        raise ValueError("Неверная или системная схема")
    columns = relationships.get("identifiers")
    if not isinstance(columns, list):
        raise ValueError("Отсутствует identifiers")
    selected = []
    seen = set()
    for item in columns:
        if not isinstance(item, dict):
            raise ValueError("Неверная запись идентификатора")
        table, column = item.get("table"), item.get("column")
        if not isinstance(table, str) or not table or not isinstance(column, str) or not column:
            raise ValueError("Пустое имя таблицы или поля")
        key = (table, column)
        if key in seen:
            raise ValueError("Повторный идентификатор в отчёте")
        seen.add(key)
        if item.get("dominant_prefix") is None:
            selected.append({"table": table, "column": column})
    return schema, selected


def query_identifier_stats(schema: str, table: str, column: str) -> sql.Composable:
    if not schema or not table or not column:
        raise ValueError("Пустое имя объекта БД")
    key = sql.SQL("NULLIF(btrim({}::text), '')").format(sql.Identifier(column))
    return sql.SQL(
        "WITH value_counts AS ("
        "SELECT {} AS key, count(*) AS occurrences FROM {} GROUP BY 1"
        ") "
        "SELECT "
        "coalesce(sum(occurrences), 0)::bigint, "
        "coalesce(sum(occurrences) FILTER (WHERE key IS NULL), 0)::bigint, "
        "count(*) FILTER (WHERE key IS NOT NULL), "
        "count(*) FILTER (WHERE key IS NOT NULL AND occurrences > 1), "
        "coalesce(sum(occurrences) FILTER "
        "(WHERE key IS NOT NULL AND occurrences > 1), 0)::bigint, "
        "coalesce(max(occurrences) FILTER (WHERE key IS NOT NULL), 0)::bigint, "
        "count(*) FILTER (WHERE key IS NOT NULL AND key ~ '^[0-9]+$') "
        "FROM value_counts"
    ).format(key, sql.Identifier(schema, table))


def audit_identifier(conn, schema: str, table: str, column: str) -> dict:
    exists = conn.execute(
        "SELECT 1 FROM information_schema.columns "
        "WHERE table_schema=%s AND table_name=%s AND column_name=%s",
        (schema, table, column),
    ).fetchone()
    if exists is None:
        raise ValueError(f"Колонка {schema}.{table}.{column} не найдена или недоступна")
    row = conn.execute(query_identifier_stats(schema, table, column)).fetchone()
    total, missing, distinct, repeated, rows_repeated, maximum, numeric = row
    return {
        "table": table,
        "column": column,
        "status": "checked",
        "total_rows": total,
        "missing_rows": missing,
        "distinct_keys": distinct,
        "duplicate_rows": total - missing - distinct,
        "repeating_keys": repeated,
        "rows_in_repeating_keys": rows_repeated,
        "max_rows_per_key": maximum,
        "numeric_only_keys": numeric,
        "all_nonempty_keys_numeric": bool(distinct and distinct == numeric),
        "can_group_by": bool(distinct),
        "can_auto_join": False,
        "business_relation_verified": False,
    }


def audit_unprefixed(conn, relations: dict, progress=None) -> dict:
    schema, columns = unprefixed_identifiers(relations)
    output = []
    for index, item in enumerate(columns, 1):
        if progress is not None:
            progress(f"[{index}/{len(columns)}] {item['table']}: {item['column']}")
        try:
            row = audit_identifier(conn, schema, item["table"], item["column"])
        except (psycopg.Error, ValueError) as exc:
            row = {**item, "status": "error", "error": str(exc)[:300],
                   "can_auto_join": False}
        output.append(row)
    return {
        "schema": schema,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_relationships_generated_at_utc": relations.get("generated_at_utc"),
        "source_modified": False,
        "summary": {
            "unprefixed_identifier_fields": len(columns),
            "checked_fields": sum(c["status"] == "checked" for c in output),
            "failed_fields": sum(c["status"] != "checked" for c in output),
            "groupable_fields": sum(c.get("can_group_by") is True for c in output),
            "automatically_approved_joins": 0,
        },
        "identifiers": output,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Проверка непrefиксных ID без изменения таблиц PostgreSQL"
    )
    parser.add_argument("--relationships", type=Path,
                        default=Path("data/relationships.json"))
    parser.add_argument("--output", type=Path,
                        default=Path("data/unprefixed-id-audit.json"))
    args = parser.parse_args()
    try:
        relations = json.loads(args.relationships.read_text(encoding="utf-8"))
        with connect() as conn:
            report = audit_unprefixed(
                conn, relations,
                progress=lambda value: print(value, file=sys.stderr, flush=True)
            )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
        print(f"Отчёт: {args.output}")
        return 1 if report["summary"]["failed_fields"] else 0
    except (ValueError, OSError, psycopg.Error) as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
