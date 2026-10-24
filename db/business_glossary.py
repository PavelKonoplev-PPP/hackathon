#!/usr/bin/env python3
"""BI-37: business glossary proposals + explicit human approval.

The schema catalog is technical evidence, not proof of business semantics.
Manual definitions live in a separate JSON file and survive regeneration.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import sys
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CATALOG = ROOT / "data/schema-catalog.json"
DEFAULT_OVERRIDES = ROOT / "data/business-glossary-overrides.json"
DEFAULT_OUTPUT = ROOT / "data/business-glossary.json"
VERSION = 1

# Hints describe field labels, NOT a verified business formula or business rule.
NAME_HINTS = {
    "Канал продажи": "Канал продажи, указанный в исходной записи.",
    "Тип операции": "Тип операции, записанный в исходных данных.",
    "Количество, шт.": "Количество в штуках согласно исходной записи.",
    "Цена до скидки за единицу, руб.": "Цена за единицу до скидки согласно исходной записи.",
    "Себестоимость единицы, руб.": "Себестоимость единицы согласно исходной записи.",
    "Дата операции (МСК)": "Дата и время операции в московском времени согласно исходной записи.",
}

def _nonempty(value, label: str, limit: int = 500) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit or "\0" in value:
        raise ValueError(f"{label}: ожидается непустая строка до {limit} символов")
    return value.strip()


def _key(schema: str, table: str, column: str) -> tuple[str, str, str]:
    return (_nonempty(schema, "schema", 63),
            _nonempty(table, "table", 63),
            _nonempty(column, "column", 63))


def _kind(column: dict) -> str:
    return str(column.get("inferred_type") or "unknown")


def _signature(column: dict) -> dict:
    return {"postgres_type": column.get("postgres_type"),
            "inferred_type": column.get("inferred_type")}


def _propose(column: dict) -> dict:
    name = column["name"]
    kind = _kind(column)
    description = NAME_HINTS.get(name)
    if description is None:
        if kind == "identifier":
            description = "Идентификатор из исходных данных; возможные связи не подтверждены."
        elif kind in {"decimal", "integer"}:
            description = "Числовое поле; единица измерения и правила расчёта не подтверждены."
        elif kind in {"date", "timestamp"}:
            description = "Дата или время из исходных данных; смысл события требует подтверждения."
        elif kind == "category":
            description = "Категория из исходных данных; значения и бизнес-смысл требуют подтверждения."
        else:
            description = "Поле исходных данных; бизнес-смысл требует подтверждения."
    unit = ("RUB" if re.search(r",\s*руб\.", name, re.IGNORECASE)
            else "pcs" if re.search(r",\s*шт\.", name, re.IGNORECASE) else None)
    return {"label": name, "description": description, "unit": unit,
            "synonyms": [], "status": "proposed", "source": "heuristic"}


def _validate_catalog(catalog: dict) -> tuple[str, list[tuple[str, dict]]]:
    if not isinstance(catalog, dict) or not isinstance(catalog.get("tables"), list):
        raise ValueError("Ожидается каталог schema-catalog.json с массивом tables")
    schema = _nonempty(catalog.get("schema"), "schema", 63)
    if schema.startswith("pg_") or schema == "information_schema":
        raise ValueError("Системная схема недопустима")
    found = set()
    entries = []
    for table in catalog["tables"]:
        if not isinstance(table, dict) or not isinstance(table.get("columns"), list):
            raise ValueError("Некорректная таблица в каталоге")
        name = _nonempty(table.get("name"), "table", 63)
        for col in table["columns"]:
            if not isinstance(col, dict):
                raise ValueError("Некорректное поле каталога")
            k = _key(schema, name, col.get("name"))
            if k in found:
                raise ValueError(f"Повторяющееся поле: {k}")
            found.add(k)
            entries.append((name, col))
    return schema, entries


def _validate_overrides(data: dict) -> dict[tuple[str, str, str], dict]:
    if not isinstance(data, dict) or data.get("version") != VERSION or not isinstance(data.get("entries"), list):
        raise ValueError("Некорректный файл ручных определений")
    result = {}
    for item in data["entries"]:
        if not isinstance(item, dict):
            raise ValueError("Ручное определение должно быть объектом")
        k = _key(item.get("schema"), item.get("table"), item.get("column"))
        if k in result:
            raise ValueError(f"Повторяющееся ручное определение: {k}")
        _nonempty(item.get("description"), "description")
        _nonempty(item.get("label"), "label", 120)
        synonyms = item.get("synonyms", [])
        if not isinstance(synonyms, list) or len(synonyms) > 20:
            raise ValueError("synonyms должен быть списком до 20 элементов")
        for s in synonyms:
            _nonempty(s, "synonym", 120)
        unit = item.get("unit")
        if unit is not None:
            _nonempty(unit, "unit", 60)
        signature = item.get("type_signature")
        if not isinstance(signature, dict):
            raise ValueError("Отсутствует type_signature ручного определения")
        result[k] = item
    return result


def build_glossary(catalog: dict, overrides: dict | None = None) -> dict:
    """Pure function: regenerated proposals never overwrite manual approvals."""
    schema, entries = _validate_catalog(catalog)
    manual = _validate_overrides(overrides or {"version": VERSION, "entries": []})
    glossary = []
    for table, col in entries:
        key = (schema, table, col["name"])
        proposal = _propose(col)
        entry = {"schema": schema, "table": table, "column": col["name"],
                 "postgres_type": col.get("postgres_type"),
                 "inferred_type": col.get("inferred_type"),
                 "conversion_policy": (col.get("conversion") or {}).get("policy"),
                 **proposal}
        if key in manual:
            saved = manual[key]
            unchanged = saved["type_signature"] == _signature(col)
            entry.update({
                "label": saved["label"],
                "description": saved["description"],
                "unit": saved.get("unit"),
                "synonyms": saved.get("synonyms", []),
                "source": "human",
                "status": "approved" if unchanged else "needs_review",
                "reviewed_at_utc": saved.get("reviewed_at_utc"),
            })
            if not unchanged:
                entry["warning"] = "Тип поля изменился; требуется повторное подтверждение"
        glossary.append(entry)
    return {
        "version": VERSION, "schema": schema,
        "source_catalog_generated_at_utc": catalog.get("generated_at_utc"),
        "entries": glossary,
        "summary": {
            "fields": len(glossary),
            "approved": sum(e["status"] == "approved" for e in glossary),
            "proposed": sum(e["status"] == "proposed" for e in glossary),
            "needs_review": sum(e["status"] == "needs_review" for e in glossary),
        },
    }


def _load(path: Path, *, optional: bool = False) -> dict:
    if optional and not path.exists():
        return {"version": VERSION, "entries": []}
    result = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(result, dict):
        raise ValueError("JSON должен содержать объект")
    return result


def _atomic_json(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    name = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8",
                 dir=path.parent, prefix=f".{path.name}.", suffix=".tmp",
                 delete=False) as file:
            name = Path(file.name)
            json.dump(obj, file, ensure_ascii=False, indent=2)
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())
        os.replace(name, path)
    finally:
        if name is not None:
            name.unlink(missing_ok=True)


@contextmanager
def _lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with (path.parent / ".business-glossary.lock").open("a+b") as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def update_glossary(catalog_path: Path = DEFAULT_CATALOG,
                    overrides_path: Path = DEFAULT_OVERRIDES,
                    output_path: Path = DEFAULT_OUTPUT, *,
                    approve: dict | None = None) -> dict:
    """Build/rebuild glossary, optionally approve a reviewed field definition.

    Raw tables and technical schema-catalog.json are never modified.
    """
    with _lock(Path(overrides_path)):
        catalog = _load(Path(catalog_path))
        schema, entries = _validate_catalog(catalog)
        saved = _load(Path(overrides_path), optional=True)
        manual = _validate_overrides(saved)
        if approve is not None:
            table = _nonempty(approve.get("table"), "table", 63)
            column = _nonempty(approve.get("column"), "column", 63)
            match = next((c for t, c in entries if t == table and c["name"] == column), None)
            if match is None:
                raise ValueError(f"Поле {schema}.{table}.{column} отсутствует в каталоге")
            key = (schema, table, column)
            synonyms = list(dict.fromkeys(approve.get("synonyms") or []))
            default = _propose(match)
            entry = {
                "schema": schema, "table": table, "column": column,
                "label": approve.get("label") or (manual.get(key) or {}).get("label") or default["label"],
                "description": approve.get("description"),
                "unit": approve.get("unit"),
                "synonyms": synonyms,
                "type_signature": _signature(match),
                "reviewed_at_utc": datetime.now(timezone.utc).isoformat(),
            }
            manual[key] = entry
            saved = {"version": VERSION, "entries": list(manual.values())}

        glossary = build_glossary(catalog, saved)  # Validate before writing.
        if approve is not None:
            _atomic_json(Path(overrides_path), saved)
        _atomic_json(Path(output_path), glossary)  # Published last.
        return glossary


def main() -> int:
    parser = argparse.ArgumentParser(description="BI-37: автословарь + ручное подтверждение полей")
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--overrides", type=Path, default=DEFAULT_OVERRIDES)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    subs = parser.add_subparsers(dest="command", required=True)
    subs.add_parser("build", help="Обновить автопредложения, сохранив ручные определения")
    approved = subs.add_parser("approve", help="Подтвердить бизнес-смысл поля вручную")
    approved.add_argument("--table", required=True)
    approved.add_argument("--column", required=True)
    approved.add_argument("--description", required=True)
    approved.add_argument("--label")
    approved.add_argument("--unit")
    approved.add_argument("--synonym", action="append", default=[])
    args = parser.parse_args()
    changes = None
    if args.command == "approve":
        changes = {"table": args.table, "column": args.column,
                   "description": args.description, "label": args.label,
                   "unit": args.unit, "synonyms": args.synonym}
    try:
        result = update_glossary(args.catalog, args.overrides, args.output,
                                 approve=changes)
        print(json.dumps(result["summary"], ensure_ascii=False))
        print(f"Словарь: {args.output}")
        return 0
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        print(f"Ошибка BI-37: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
