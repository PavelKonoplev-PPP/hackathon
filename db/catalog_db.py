#!/usr/bin/env python3
"""Объединить JSON-профиль и полный аудит в каталог для backend без SQL/БД."""
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    from .safe_cast import CAST_TYPES
except ImportError:
    from safe_cast import CAST_TYPES


def build_catalog(profile: dict, audit: dict, *, include_examples: bool = False) -> dict:
    """Строит каталог: тип по выборке отдельно от подтверждённого полного аудита."""
    if not isinstance(profile, dict) or not isinstance(audit, dict):
        raise ValueError("Профиль и аудит должны быть JSON-объектами")
    schema = profile.get("schema")
    if not isinstance(schema, str) or not schema or schema != audit.get("schema"):
        raise ValueError("Несовпадение схемы профиля и аудита")
    stamp = profile.get("generated_at_utc")
    if not stamp or stamp != audit.get("source_profile_generated_at_utc"):
        raise ValueError("Аудит устарел: он относится к другой версии профиля")
    if audit.get("statistics_are_full_column") is not True:
        raise ValueError("Аудит не подтверждает проверку полных колонок")
    if not isinstance(profile.get("tables"), list) or not isinstance(audit.get("columns"), list):
        raise ValueError("Некорректная структура профиля или аудита")

    indexed = {}
    for entry in audit["columns"]:
        if not isinstance(entry, dict):
            raise ValueError("Некорректная запись аудита")
        key = (entry.get("table"), entry.get("column"))
        if any(not isinstance(part, str) or not part for part in key) or key in indexed:
            raise ValueError("Отсутствующий/повторяющийся ключ аудита")
        indexed[key] = entry

    consumed = set()
    seen = set()
    totals = dict(tables=0, columns=0, cast_candidates=0,
                  safe_complete=0, safe_with_nulls=0, blocked_invalid=0,
                  unverified=0, not_applicable=0)
    tables = []
    for table in profile["tables"]:
        if not isinstance(table, dict) or not isinstance(table.get("name"), str):
            raise ValueError("Неверная таблица в профиле")
        table_name = table["name"]
        if not isinstance(table.get("columns"), list):
            raise ValueError("Нет списка колонок")
        totals["tables"] += 1
        columns = []
        for col in table["columns"]:
            if not isinstance(col, dict) or not isinstance(col.get("name"), str):
                raise ValueError("Неверная колонка в профиле")
            name = col["name"]
            key = (table_name, name)
            if key in seen:
                raise ValueError(f"Повторяющееся поле {key}")
            seen.add(key)
            totals["columns"] += 1
            kind = col.get("inferred_type")
            result = {
                "name": name,
                "postgres_type": col.get("postgres_type"),
                "nullable": col.get("nullable"),
                "inferred_type": kind,
                "confidence": col.get("confidence"),
                "inference_basis": col.get("inference_basis", "legacy_profile"),
                "sample_rows": col.get("sample_rows"),
                "sample_nonempty_rows": col.get("sample_nonempty_rows"),
                "sample_distinct": col.get("distinct_in_sample"),
                "numeric_format": col.get("numeric_format"),
                "warning": col.get("warning"),
            }
            if include_examples:
                result["examples"] = col.get("examples", [])
            conversion = {"target_type": None, "policy": "not_applicable",
                          "quality": None}
            if kind not in CAST_TYPES:
                totals["not_applicable"] += 1
                if key in indexed:
                    raise ValueError(f"Неожиданный аудит неподдерживаемого типа: {key}")
            else:
                conversion["target_type"] = kind
                totals["cast_candidates"] += 1
                item = indexed.get(key)
                if item is None or item.get("status") != "checked":
                    policy = "unverified"
                    if item is not None:
                        consumed.add(key)
                        conversion["audit_error"] = item.get("error")
                else:
                    consumed.add(key)
                    if item.get("target_type") != kind:
                        raise ValueError(f"Тип в аудите не совпадает с профилем: {key}")
                    names = ("total_rows", "missing_rows", "invalid_rows", "convertible_rows")
                    counts = tuple(item.get(k) for k in names)
                    if any(type(n) is not int or n < 0 for n in counts):
                        raise ValueError(f"Некорректные счётчики: {key}")
                    total, missing, invalid, convertible = counts
                    if missing + invalid + convertible != total:
                        raise ValueError(f"Несогласованные счётчики: {key}")
                    conversion["quality"] = dict(zip(names, counts))
                    if invalid:
                        policy = "blocked_invalid"
                    elif not convertible:
                        policy = "unverified"
                    elif missing:
                        policy = "safe_with_nulls"
                    else:
                        policy = "safe_complete"
                conversion["policy"] = policy
                totals[policy] += 1
            result["conversion"] = conversion
            columns.append(result)
        tables.append({
            "name": table_name,
            "estimated_total_rows": table.get("estimated_total_rows"),
            "sampled_rows": table.get("sampled_rows"),
            "columns": columns,
        })
    if indexed.keys() - consumed:
        raise ValueError("В аудите найдены колонки, которых нет в профиле")
    return {
        "catalog_version": 1,
        "schema": schema,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_profile_generated_at_utc": stamp,
        "source_audit_generated_at_utc": audit.get("generated_at_utc"),
        "confidence_is_heuristic": True,
        "relationships_verified": False,
        "summary": totals,
        "tables": tables,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Профиль + аудит -> каталог для backend")
    parser.add_argument("--profile", type=Path, default=Path("data/all-profile.json"))
    parser.add_argument("--audit", type=Path, default=Path("data/type-audit.json"))
    parser.add_argument("--output", type=Path, default=Path("data/schema-catalog.json"))
    parser.add_argument("--include-examples", action="store_true",
                        help="Включить значения примеров, если это допустимо")
    args = parser.parse_args()
    try:
        profile = json.loads(args.profile.read_text(encoding="utf-8"))
        audit = json.loads(args.audit.read_text(encoding="utf-8"))
        catalog = build_catalog(profile, audit, include_examples=args.include_examples)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(catalog, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(json.dumps(catalog["summary"], ensure_ascii=False, indent=2))
        print(f"Каталог: {args.output}")
    except (OSError, ValueError) as exc:
        print(f"Ошибка каталога: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
