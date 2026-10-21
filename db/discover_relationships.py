#!/usr/bin/env python3
"""Поиск кандидатов на JOIN между идентификаторами неизвестных PostgreSQL-таблиц.

Разделяет технически проверенную ссылочную целостность и бизнес-подтверждение.
Не изменяет данные; не выводит значения идентификаторов, только общие префиксы.
"""
import argparse
import json
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import psycopg
from psycopg import sql

try:
    from .profile_db import connect
except ImportError:
    from profile_db import connect

REFERENCE_WORDS = ("исходн", "предыдущ", "родител", "previous", "original", "parent")
SUFFIX = re.compile(r"^(.+?)([0-9]+)$")
MAX_PAIRS = 20
MAX_SAMPLE = 1000
MAX_COLUMNS = 100


def identifier_columns(catalog: dict) -> tuple[str, list[dict]]:
    if not isinstance(catalog, dict):
        raise ValueError("Каталог должен быть JSON-объектом")
    schema = catalog.get("schema")
    if not isinstance(schema, str) or not schema or schema == "information_schema" or schema.startswith("pg_"):
        raise ValueError("Запрещённая или пустая схема")
    if not isinstance(catalog.get("tables"), list):
        raise ValueError("Нет списка таблиц в каталоге")
    result = []
    seen = set()
    for table in catalog["tables"]:
        if not isinstance(table, dict) or not isinstance(table.get("name"), str):
            raise ValueError("Некорректная таблица в каталоге")
        if not isinstance(table.get("columns"), list):
            raise ValueError("Нет колонок таблицы")
        for col in table["columns"]:
            if not isinstance(col, dict):
                raise ValueError("Некорректная колонка")
            if col.get("inferred_type") != "identifier":
                continue
            name = col.get("name")
            if not isinstance(name, str) or not name:
                raise ValueError("Нет имени идентификатора")
            key = (table["name"], name)
            if key in seen:
                raise ValueError(f"Повторяющийся идентификатор: {key}")
            seen.add(key)
            result.append({"table": table["name"], "column": name})
    if len(result) > MAX_COLUMNS:
        raise ValueError(f"Слишком много идентификаторов (максимум {MAX_COLUMNS})")
    return schema, result


def infer_prefix(values: list[str]) -> dict:
    """Доминантный непустой префикс перед числовым суффиксом по выборке."""
    values = [v.strip() for v in values if isinstance(v, str) and v.strip()]
    prefixes = []
    for value in values:
        match = SUFFIX.fullmatch(value)
        if match and len(match.group(1)) >= 2:
            prefixes.append(match.group(1))
    counts = Counter(prefixes)
    prefix, top = counts.most_common(1)[0] if counts else (None, 0)
    # Префикс нельзя считать подтверждённым по 1-2 строкам или части выборки.
    dominant = prefix if len(values) >= 10 and top / len(values) >= 0.95 else None
    return {"sample_nonempty": len(values), "dominant_prefix": dominant,
            "dominant_ratio": round(top / len(values), 4) if values else 0.0}


def sample_identifier(conn, schema: str, table: str, column: str,
                      sample_size: int) -> dict:
    # Небольшая доля физических блоков; WHERE берёт только заполненные значения.
    query = sql.SQL(
        "SELECT {} FROM {} TABLESAMPLE SYSTEM (2) "
        "WHERE NULLIF(btrim({}::text), '') IS NOT NULL "
        "ORDER BY random() LIMIT %s"
    ).format(sql.Identifier(column), sql.Identifier(schema, table),
             sql.Identifier(column))
    rows = conn.execute(query, (sample_size,)).fetchall()
    return infer_prefix([str(r[0]) for r in rows if r[0] is not None])


def reference_like(name: str) -> bool:
    return any(word in name.casefold() for word in REFERENCE_WORDS)


def choose_pairs(columns: list[dict], max_pairs: int) -> tuple[list[dict], int]:
    """Только совпадающие доминирующие префиксы; никаких JOIN по числовой части."""
    if max_pairs < 1:
        raise ValueError("max_pairs должен быть положительным")
    matches = []
    different_prefix = 0
    for i, left in enumerate(columns):
        for right in columns[i + 1:]:
            first = left.get("dominant_prefix")
            second = right.get("dominant_prefix")
            if not first or not second:
                continue
            if first != second:
                different_prefix += 1
                continue
            is_self = left["table"] == right["table"]
            # Для самоссылок ставим очевидное ссылочное поле слева.
            if is_self and reference_like(left["column"]) != reference_like(right["column"]):
                source, target = ((left, right) if reference_like(left["column"])
                                  else (right, left))
                matches.append({"source": source, "target": target,
                                "kind": "self_reference_candidate"})
            elif not is_self:
                # Межтабличные пары оставляем техническими гипотезами; направление
                # выбирается для проверки, но не означает семантику FK.
                matches.append({"source": left, "target": right,
                                "kind": "cross_table_candidate"})
            # Если в одной таблице два однотипных ключа без ссылочного названия,
            # не угадываем направление связи.
    if len(matches) > max_pairs:
        raise ValueError(
            f"Найдено {len(matches)} кандидатов; лимит {max_pairs}. "
            "Увеличьте --max-pairs после проверки стоимости запросов."
        )
    return matches, different_prefix


def column_stats(conn, schema: str, table: str, column: str) -> dict:
    key = sql.SQL("NULLIF(btrim({}::text), '')").format(sql.Identifier(column))
    query = sql.SQL(
        "SELECT count(*), count(*) FILTER (WHERE {} IS NULL), "
        "count(DISTINCT {}) FROM {}"
    ).format(key, key, sql.Identifier(schema, table))
    count, missing, distinct = conn.execute(query).fetchone()
    return {"total_rows": count, "missing_rows": missing,
            "distinct_keys": distinct,
            "duplicate_rows": count - missing - distinct}


def check_reference(conn, schema: str, source: dict, target: dict) -> dict:
    """Учитывает только exact equality по полному ID; не размножает строки."""
    left = sql.SQL("NULLIF(btrim({}::text), '')").format(
        sql.Identifier(source["column"]))
    right = sql.SQL("NULLIF(btrim({}::text), '')").format(
        sql.Identifier(target["column"]))
    query = sql.SQL(
        "WITH target_keys AS (SELECT DISTINCT {} AS key FROM {}), "
        "source_keys AS (SELECT {} AS key FROM {}) "
        "SELECT count(*) FILTER (WHERE source_keys.key IS NOT NULL), "
        "count(*) FILTER (WHERE source_keys.key IS NOT NULL "
        "AND target_keys.key IS NOT NULL) "
        "FROM source_keys LEFT JOIN target_keys "
        "ON source_keys.key = target_keys.key"
    ).format(
        right, sql.Identifier(schema, target["table"]),
        left, sql.Identifier(schema, source["table"]),
    )
    nonempty, matched = conn.execute(query).fetchone()
    stats = column_stats(conn, schema, target["table"], target["column"])
    unmatched = nonempty - matched
    return {
        "source_nonempty_rows": nonempty,
        "matched_source_rows": matched,
        "unmatched_source_rows": unmatched,
        "target_key_stats": stats,
        "target_unique_nonnull": stats["duplicate_rows"] == 0,
        "referential_integrity_verified": (
            nonempty > 0 and unmatched == 0
            and stats["duplicate_rows"] == 0 and stats["missing_rows"] == 0
        ),
        "business_semantics_approved": False,
        "can_auto_join": False,
    }


def discover_relationships(conn, catalog: dict, *, sample_size: int = MAX_SAMPLE,
                           max_pairs: int = MAX_PAIRS, progress=None) -> dict:
    if not 10 <= sample_size <= MAX_SAMPLE:
        raise ValueError(f"sample_size: допустимо 10..{MAX_SAMPLE}")
    schema, ids = identifier_columns(catalog)
    if progress is not None:
        progress(f"Найдено идентификаторов: {len(ids)}")
    specs = []
    for n, item in enumerate(ids, start=1):
        if progress is not None:
            progress(f"[{n}/{len(ids)}] {item['table']}: {item['column']}")
        observed = sample_identifier(conn, schema, item["table"],
                                     item["column"], sample_size)
        specs.append({**item, **observed})
    pairs, skipped = choose_pairs(specs, max_pairs)
    results = []
    for n, pair in enumerate(pairs, start=1):
        if progress is not None:
            progress(f"Проверка ссылок [{n}/{len(pairs)}]: "
                     f"{pair['source']['table']}:{pair['source']['column']} -> "
                     f"{pair['target']['table']}:{pair['target']['column']}")
        edge = {
            "kind": pair["kind"],
            "source": {k: pair["source"][k] for k in ("table", "column")},
            "target": {k: pair["target"][k] for k in ("table", "column")},
            "matching_prefix": pair["source"]["dominant_prefix"],
            "direction_is_hypothesis": pair["kind"] == "cross_table_candidate",
        }
        try:
            evidence = check_reference(conn, schema, pair["source"], pair["target"])
            edge.update(evidence)
            edge["status"] = (
                "technical_reference_verified"
                if evidence["referential_integrity_verified"]
                else "not_verified"
            )
        except psycopg.Error as exc:
            edge.update(status="query_error", error=str(exc)[:300],
                        business_semantics_approved=False, can_auto_join=False)
        results.append(edge)
    return {
        "version": 1,
        "schema": schema,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_catalog_generated_at_utc": catalog.get("generated_at_utc"),
        "source_modified": False,
        "join_approval_required": True,
        "prefix_detection_is_sample_based": True,
        "summary": {
            "identifier_columns": len(specs),
            "columns_with_dominant_prefix": sum(bool(x["dominant_prefix"]) for x in specs),
            "pairs_skipped_different_prefix": skipped,
            "candidate_pairs": len(pairs),
            "technically_verified": sum(
                x["status"] == "technical_reference_verified" for x in results
            ),
            "approved_joins": 0,
        },
        "identifiers": specs,
        "relationships": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Поиск потенциальных связей без изменения исходных данных"
    )
    parser.add_argument("--catalog", type=Path, default=Path("data/schema-catalog.json"))
    parser.add_argument("--output", type=Path, default=Path("data/relationships.json"))
    parser.add_argument("--sample-size", type=int, default=MAX_SAMPLE)
    parser.add_argument("--max-pairs", type=int, default=MAX_PAIRS)
    args = parser.parse_args()
    try:
        catalog = json.loads(args.catalog.read_text(encoding="utf-8"))
        with connect() as conn:
            report = discover_relationships(
                conn, catalog, sample_size=args.sample_size,
                max_pairs=args.max_pairs,
                progress=lambda msg: print(msg, file=sys.stderr, flush=True)
            )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
        print(f"Отчёт: {args.output}")
        return 1 if any(x["status"] == "query_error" for x in report["relationships"]) else 0
    except (ValueError, OSError, psycopg.Error) as exc:
        print(f"Ошибка обнаружения связей: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
