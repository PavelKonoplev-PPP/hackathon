"""Обновление профиля и каталога для SQL-агента после импорта таблицы.

Инкрементально аудирует только изменившуюся таблицу, сохраняя метаданные
остальных. Если полного кэша нет, строит каталог всей схемы заново.
Файлы публикуются только после успешной сборки нового каталога.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

try:
    import fcntl
except ImportError:
    fcntl = None  # type: ignore

import psycopg

try:
    from .audit_profile import audit_profile
    from .catalog_db import build_catalog
    from .profile_db import connect, get_tables, profile_database
except ImportError:  # Support python db/refresh_catalog.py
    from audit_profile import audit_profile
    from catalog_db import build_catalog
    from profile_db import connect, get_tables, profile_database


FILES = {
    "profile": "all-profile.json",
    "audit": "type-audit.json",
    "catalog": "schema-catalog.json",
}


@contextmanager
def _locked_directory(folder: Path):
    folder.mkdir(parents=True, exist_ok=True)
    # Serialize concurrent uploads. Lock file is not part of the published catalog.
    with (folder / ".catalog-refresh.lock").open("a+b") as handle:
        if fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _load_previous(folder: Path, schema: str) -> tuple[dict, dict] | None:
    paths = [folder / name for name in FILES.values()]
    if not all(path.is_file() for path in paths):
        return None
    profile, audit, catalog = (
        json.loads(path.read_text(encoding="utf-8")) for path in paths
    )
    if any(not isinstance(item, dict) for item in (profile, audit, catalog)):
        raise ValueError("Каталоги должны быть JSON-объектами")
    if profile.get("schema") != schema or audit.get("schema") != schema or catalog.get("schema") != schema:
        raise ValueError("Существующие каталоги относятся к другой схеме")
    if (
        catalog.get("source_profile_generated_at_utc") != profile.get("generated_at_utc")
        or catalog.get("source_audit_generated_at_utc") != audit.get("generated_at_utc")
    ):
        raise ValueError("Существующие каталоги рассинхронизированы; выполните полную пересборку")
    # Refuse to use corrupt/stale conversion policies as the baseline.
    build_catalog(profile, audit)
    return profile, audit


def _audit_summary(columns: list[dict]) -> dict:
    checked = [x for x in columns if x.get("status") == "checked"]
    return {
        "candidate_columns": len(columns),
        "checked_columns": len(checked),
        "failed_columns": len(columns) - len(checked),
        "columns_with_invalid_values": sum(x.get("invalid_rows", 0) > 0 for x in checked),
        "columns_with_missing_values": sum(x.get("missing_rows", 0) > 0 for x in checked),
        "fully_convertible_columns": sum(
            x.get("invalid_rows", 0) == 0 and x.get("missing_rows", 0) == 0
            for x in checked
        ),
    }


def _merge_reports(
    old_profile: dict, old_audit: dict, fresh_profile: dict, fresh_audit: dict,
    table_name: str,
) -> tuple[dict, dict]:
    if len(fresh_profile["tables"]) != 1 or fresh_profile["tables"][0]["name"] != table_name:
        raise ValueError("Профиль обновляемой таблицы неполный")

    profile = copy.deepcopy(old_profile)
    profile["generated_at_utc"] = fresh_profile["generated_at_utc"]
    profile["sampling"] = fresh_profile["sampling"]
    profile["tables"] = sorted(
        [t for t in profile["tables"] if t["name"] != table_name] + fresh_profile["tables"],
        key=lambda t: t["name"],
    )

    audit = copy.deepcopy(old_audit)
    audit["generated_at_utc"] = fresh_audit["generated_at_utc"]
    audit["source_profile_generated_at_utc"] = profile["generated_at_utc"]
    audit["columns"] = [
        col for col in audit["columns"] if col["table"] != table_name
    ] + fresh_audit["columns"]
    audit["summary"] = _audit_summary(audit["columns"])
    return profile, audit


def _save_atomic(path: Path, data: dict) -> None:
    encoded = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    tmp = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent,
            prefix=f".{path.name}.", suffix=".tmp", delete=False,
        ) as stream:
            tmp = Path(stream.name)
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
    finally:
        if tmp is not None:
            tmp.unlink(missing_ok=True)


def refresh_catalog(
    conn,
    *,
    table: str | None = None,
    schema: str = "raw",
    data_dir: Path = Path("data"),
    sample_size: int = 300,
) -> dict:
    """Публикует каталог для всех таблиц; возвращает краткий отчёт обновления.

    Backend должен вызывать ПОСЛЕ успешного commit импорта CSV и использовать
    read-only соединение. Файлы data_dir не являются входом от пользователя.
    Несвязанные таблицы не переаудируются, если полный кэш уже существует.
    """
    if schema != "raw":
        raise ValueError("Этот локальный каталог предназначен только для схемы raw")
    if table is not None and (not isinstance(table, str) or not table.strip()):
        raise ValueError("Нужно указать существующее имя таблицы")
    data_dir = Path(data_dir)
    with _locked_directory(data_dir):
        previous = _load_previous(data_dir, schema)
        mode = "full"
        preserved = []

        if table and previous is not None:
            current_tables = {name for name, _ in get_tables(conn, schema, 100)}
            previous_tables = {x["name"] for x in previous[0]["tables"]}
            # If other tables appeared/disappeared, do a complete rebuild.
            if (current_tables - {table}) == (previous_tables - {table}):
                mode = "incremental"
                fresh_profile = profile_database(
                    conn, schema=schema, table=table,
                    sample_size=sample_size, include_examples=False,
                )
                fresh_audit = audit_profile(conn, fresh_profile)
                profile, audit = _merge_reports(
                    previous[0], previous[1], fresh_profile, fresh_audit, table,
                )
                preserved = sorted(previous_tables - {table})

        if mode == "full":
            profile = profile_database(
                conn, schema=schema, sample_size=sample_size, include_examples=False,
            )
            audit = audit_profile(conn, profile)

        catalog = build_catalog(profile, audit)
        catalog["refresh"] = {
            "mode": mode,
            "table": table if mode == "incremental" else None,
            "preserved_prior_tables": preserved,
            "relationships_require_revalidation": True,
        }

        # Build and validate everything BEFORE touching any existing files.
        # The SQL agent reads only schema-catalog.json, which is written LAST.
        _save_atomic(data_dir / FILES["profile"], profile)
        _save_atomic(data_dir / FILES["audit"], audit)
        _save_atomic(data_dir / FILES["catalog"], catalog)
        return {
            "mode": mode,
            "schema": schema,
            "updated_table": table,
            "tables": len(catalog["tables"]),
            "columns": catalog["summary"]["columns"],
            "unverified_columns": catalog["summary"]["unverified"],
            "failed_audits": audit["summary"]["failed_columns"],
            "catalog_path": str(data_dir / FILES["catalog"]),
            "relationships_require_revalidation": True,
        }


def main() -> int:
    parser = argparse.ArgumentParser(description="Обновить метаданные после импорта CSV")
    parser.add_argument("--table", help="Имя только что импортированной таблицы; без аргумента полная сборка")
    parser.add_argument("--schema", default="raw", help="Пока поддерживается схема raw")
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--sample-size", type=int, default=300)
    args = parser.parse_args()
    try:
        with connect() as conn:
            report = refresh_catalog(
                conn, table=args.table, schema=args.schema,
                data_dir=args.data_dir, sample_size=args.sample_size,
            )
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["failed_audits"] == 0 else 2
    except (OSError, ValueError, json.JSONDecodeError, psycopg.Error) as exc:
        print(f"Ошибка обновления каталога: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
