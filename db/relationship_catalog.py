#!/usr/bin/env python3
"""Финальный JSON-контракт связей и группировок для backend/LLM.

Агрегирует технические связи и статистику идентификаторов. Для новых
группировок вызывает read-only аудит; JOIN никогда не одобряет автоматически.
"""
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import psycopg

try:
    from .profile_db import connect
    from .audit_identifiers import audit_identifier
except ImportError:
    from profile_db import connect
    from audit_identifiers import audit_identifier


def _column_key(item: dict) -> tuple[str, str]:
    if not isinstance(item, dict):
        raise ValueError("Описание поля должно быть объектом")
    table, column = item.get("table"), item.get("column")
    if not isinstance(table, str) or not table or not isinstance(column, str) or not column:
        raise ValueError("Неверное имя таблицы/колонки")
    return (table, column)


def parse_group(spec: str) -> tuple[str, str]:
    if ":" not in spec:
        raise ValueError("Формат --group: ИМЯ_ТАБЛИЦЫ:ИМЯ_ПОЛЯ")
    table, column = spec.split(":", 1)
    if not table or not column:
        raise ValueError("Неверное имя таблицы/колонки в --group")
    return table, column


def build_relationship_catalog(
    relationships: dict, id_audit: dict, *,
    additional_groups: list[dict] | None = None,
) -> dict:
    """Создать контракт с безопасным запретом на неподтверждённый JOIN."""
    if not isinstance(relationships, dict) or not isinstance(id_audit, dict):
        raise ValueError("Отчёты должны быть JSON-объектами")
    schema = relationships.get("schema")
    if not isinstance(schema, str) or not schema or schema != id_audit.get("schema"):
        raise ValueError("Несовпадение схемы отчётов")
    relationship_ts = relationships.get("generated_at_utc")
    if not relationship_ts or id_audit.get("source_relationships_generated_at_utc") != relationship_ts:
        raise ValueError("Аудит идентификаторов относится к другой версии поиска связей")
    if not isinstance(relationships.get("identifiers"), list):
        raise ValueError("Нет identifiers в отчёте связей")
    if not isinstance(relationships.get("relationships"), list):
        raise ValueError("Нет relationships в отчёте связей")
    if not isinstance(id_audit.get("identifiers"), list):
        raise ValueError("Нет identifiers в аудите")

    all_ids = {}
    for item in relationships["identifiers"]:
        key = _column_key(item)
        if key in all_ids:
            raise ValueError(f"Дублирующийся идентификатор: {key}")
        all_ids[key] = item

    # Аудит должен охватывать все ID без доминирующего префикса.
    expected = {key for key, item in all_ids.items()
                if item.get("dominant_prefix") is None}
    actual = {}
    for item in id_audit["identifiers"]:
        key = _column_key(item)
        if key in actual:
            raise ValueError(f"Дублирующаяся запись аудита: {key}")
        actual[key] = item
    if expected != actual.keys():
        raise ValueError("Аудит неполный или содержит посторонние идентификаторы")

    extra = {}
    for item in additional_groups or []:
        key = _column_key(item)
        if key not in all_ids or key in actual or key in extra:
            raise ValueError(f"Неверная/повторная дополнительная группировка: {key}")
        extra[key] = item

    groupings = []
    for key, item in {**actual, **extra}.items():
        table, column = key
        if item.get("status") != "checked":
            groupings.append({
                "table": table, "column": column,
                "status": "unverified",
                "can_group_by": False,
                "can_auto_join": False,
            })
            continue
        names = ("total_rows", "missing_rows", "distinct_keys", "duplicate_rows",
                 "repeating_keys", "rows_in_repeating_keys", "max_rows_per_key")
        counts = {name: item.get(name) for name in names}
        if any(type(v) is not int or v < 0 for v in counts.values()):
            raise ValueError(f"Неверные числовые данные для {key}")
        if counts["missing_rows"] + counts["distinct_keys"] + counts["duplicate_rows"] != counts["total_rows"]:
            raise ValueError(f"Несогласованные количества строк для {key}")
        total = counts["total_rows"]
        groupings.append({
            "table": table,
            "column": column,
            "status": "checked",
            "can_group_by": counts["distinct_keys"] > 0,
            "can_auto_join": False,
            "missing_fraction": round(counts["missing_rows"] / total, 6) if total else None,
            "evidence": counts,
            "semantics": "same_table_grouping_only",
        })

    joins = []
    for item in relationships["relationships"]:
        source = _column_key(item.get("source"))
        target = _column_key(item.get("target"))
        if source not in all_ids or target not in all_ids:
            raise ValueError("Связь ссылается на отсутствующее поле")
        technical = (
            item.get("status") == "technical_reference_verified"
            and item.get("referential_integrity_verified") is True
            and item.get("target_unique_nonnull") is True
            and item.get("unmatched_source_rows") == 0
        )
        joins.append({
            "source": {"table": source[0], "column": source[1]},
            "target": {"table": target[0], "column": target[1]},
            "kind": item.get("kind"),
            "technical_reference_verified": technical,
            "matched_source_rows": item.get("matched_source_rows"),
            "unmatched_source_rows": item.get("unmatched_source_rows"),
            "target_unique_nonnull": item.get("target_unique_nonnull"),
            "business_semantics_approved": False,
            "can_auto_join": False,
            "policy": "manual_approval_required" if technical else "not_verified",
        })

    return {
        "catalog_version": 1,
        "schema": schema,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_relationships_generated_at_utc": relationship_ts,
        "source_identifier_audit_generated_at_utc": id_audit.get("generated_at_utc"),
        "source_modified": False,
        "rules": {
            "join_requires_explicit_business_approval": True,
            "do_not_strip_identifier_prefixes": True,
            "grouping_does_not_imply_a_join": True,
            "missing_grouping_keys_should_be_reported": True,
            "verified_relationships_are_current_dataset_only": True,
        },
        "summary": {
            "identifier_columns": len(all_ids),
            "technical_relationships": sum(x["technical_reference_verified"] for x in joins),
            "approved_joins": 0,
            "grouping_fields_checked": sum(x["status"] == "checked" for x in groupings),
            "grouping_fields_with_missing_values": sum(
                x.get("evidence", {}).get("missing_rows", 0) > 0 for x in groupings
            ),
            "grouping_fields_unverified": sum(x["status"] != "checked" for x in groupings),
        },
        "join_candidates": joins,
        "grouping_fields": groupings,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Собрать итоговый каталог связей и группировок для backend"
    )
    parser.add_argument("--relationships", type=Path,
                        default=Path("data/relationships.json"))
    parser.add_argument("--id-audit", type=Path,
                        default=Path("data/unprefixed-id-audit.json"))
    parser.add_argument("--output", type=Path,
                        default=Path("data/relationship-catalog.json"))
    parser.add_argument("--group", action="append", default=[],
                        help="Дополнительное поле группировки: ТАБЛИЦА:КОЛОНКА")
    args = parser.parse_args()
    try:
        relations = json.loads(args.relationships.read_text(encoding="utf-8"))
        id_audit = json.loads(args.id_audit.read_text(encoding="utf-8"))
        specs = [parse_group(s) for s in args.group]
        known = {_column_key(i) for i in relations.get("identifiers", [])}
        extra = []
        if specs:
            if len(specs) != len(set(specs)) or any(s not in known for s in specs):
                raise ValueError("Дополнительная группировка повторяется или отсутствует в каталоге")
            with connect() as conn:
                for table, column in specs:
                    print(f"Аудит группировки: {table}.{column}", file=sys.stderr)
                    extra.append(audit_identifier(conn, relations["schema"], table, column))
        catalog = build_relationship_catalog(relations, id_audit, additional_groups=extra)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(catalog, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(json.dumps(catalog["summary"], ensure_ascii=False, indent=2))
        print(f"Каталог: {args.output}")
    except (OSError, ValueError, psycopg.Error) as exc:
        print(f"Ошибка каталога связей: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
