"""Выполнение SQL модели в PostgreSQL: таблица `t` с уже приведёнными типами.

В исходных таблицах (схема raw) все значения — TEXT: десятичная запятая,
даты строкой. Маленькой модели тяжело писать такие преобразования, поэтому
мы оборачиваем её запрос:

    WITH t AS (SELECT <безопасные приведения колонок> FROM raw."таблица")
    SELECT * FROM (<запрос модели>) AS q LIMIT <лимит>

Какие колонки приводить, берём из каталога (db/catalog_db.py): только поля
с политикой safe_complete / safe_with_nulls. Коды и категории остаются текстом.
Логика приведения повторяет db/safe_cast.py — при изменениях держать в синхроне.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass

# Как в db/safe_cast.py: целевой тип PostgreSQL и регулярное выражение формата.
CAST_TYPES = {
    "integer": ("bigint", r"^[+-]?[0-9]+$"),
    "decimal": ("numeric", r"^[+-]?([0-9]+([.][0-9]+)?|[.][0-9]+)$"),
    "date": ("date", r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$"),
    "timestamp": (
        "timestamp without time zone",
        r"^[0-9]{4}-[0-9]{2}-[0-9]{2}[ T][0-9]{2}:[0-9]{2}(:[0-9]{2}([.][0-9]{1,6})?)?$",
    ),
}
RU_DATE_RE = r"^[0-9]{2}[.][0-9]{2}[.][0-9]{4}$"
CASTABLE_POLICIES = {"safe_complete", "safe_with_nulls"}

# Значения категорий меняются только при новой загрузке данных, а backend создаёт
# новый runner на каждый запрос — поэтому кэш общий для процесса.
_VALUES_CACHE: dict[tuple[str, str, str], dict[str, list[str]]] = {}


_RANGE_CACHE: dict[tuple[str, str, str, str], tuple] = {}


def clear_values_cache() -> None:
    """Вызвать после загрузки/обновления данных."""
    _VALUES_CACHE.clear()
    _RANGE_CACHE.clear()


def quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def quote_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def cast_sql(column: str, kind: str) -> str:
    """Безопасное приведение TEXT -> тип; неверный формат даёт NULL."""
    pg_type, pattern = CAST_TYPES[kind]
    source = f"NULLIF(btrim({quote_ident(column)}::text), '')"
    if kind == "decimal":
        normalized = f"replace({source}, ',', '.')"
    elif kind == "date":
        normalized = (f"(CASE WHEN {source} ~ {quote_literal(RU_DATE_RE)} THEN "
                      f"substr({source}, 7, 4) || '-' || substr({source}, 4, 2) || '-' || "
                      f"substr({source}, 1, 2) ELSE {source} END)")
    else:
        normalized = source
    valid = (f"({normalized} ~ {quote_literal(pattern)} AND "
             f"pg_input_is_valid({normalized}, {quote_literal(pg_type)}))")
    return f"(CASE WHEN {valid} THEN {normalized}::{pg_type} ELSE NULL::{pg_type} END)"


def column_kind(col: dict) -> str | None:
    """Тип, к которому колонку можно безопасно привести, иначе None (остаётся текстом)."""
    conversion = col.get("conversion") or {}
    kind = conversion.get("target_type")
    if kind in CAST_TYPES and conversion.get("policy") in CASTABLE_POLICIES:
        return kind
    return None


def build_query(schema: str, table: dict, model_sql: str, max_rows: int) -> str:
    """Оборачивает проверенный запрос модели: таблица t + общий лимит строк."""
    parts = []
    for col in table.get("columns") or []:
        name = col["name"]
        kind = column_kind(col)
        expr = cast_sql(name, kind) if kind else quote_ident(name)
        parts.append(f"{expr} AS {quote_ident(name)}")
    source = f"{quote_ident(schema)}.{quote_ident(table['name'])}"
    return (f"WITH t AS (SELECT {', '.join(parts)} FROM {source})\n"
            f"SELECT * FROM (\n{model_sql}\n) AS q LIMIT {int(max_rows)}")


@dataclass
class QueryResult:
    columns: list[str]
    rows: list[tuple]
    truncated: bool = False


def _connection_key(conn) -> str:
    """«host:port/dbname» из psycopg-соединения; без него — id объекта (не смешиваем)."""
    info = getattr(conn, "info", None)
    host = getattr(info, "host", None)
    if info is not None and host is not None:
        return f"{host}:{getattr(info, 'port', '')}/{getattr(info, 'dbname', '')}"
    return f"conn-{id(conn)}"


def extract_select_aliases(sql: str) -> list[str | None]:
    """Extract top-level SELECT aliases from SQL to recover names if PostgreSQL
    truncated them to 63 bytes (NAMEDATALEN limit)."""
    m = re.search(r"\bselect\b", sql, re.IGNORECASE)
    if not m:
        return []
    depth, quote, i = 0, None, m.end()
    while i < len(sql):
        ch = sql[i]
        if quote:
            quote = None if ch == quote else quote
        elif ch in "'\"":
            quote = ch
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif depth == 0 and re.match(r"from\b", sql[i:i + 5], re.IGNORECASE) and not re.match(r"[\w]", sql[i - 1]):
            break
        i += 1
    body = re.sub(r"^\s*distinct\b", "", sql[m.end():i], flags=re.IGNORECASE)
    parts, current = [], []
    d, q = 0, None
    for ch in body:
        if q:
            if ch == q:
                q = None
        elif ch in "'\"":
            q = ch
        elif ch == "(":
            d += 1
        elif ch == ")":
            d -= 1
        elif ch == "," and d == 0:
            parts.append("".join(current).strip())
            current = []
            continue
        current.append(ch)
    if current:
        parts.append("".join(current).strip())

    aliases = []
    for item in parts:
        alias_match = re.search(r'\bas\s+(?:"([^"]+)"|([a-zA-Zа-яА-ЯёЁ_][a-zA-Zа-яА-ЯёЁ0-9_]*))\s*$', item, re.IGNORECASE)
        if alias_match:
            aliases.append(alias_match.group(1) or alias_match.group(2))
        else:
            aliases.append(None)
    return aliases


def restore_column_aliases(columns: list[str], model_sql: str) -> list[str]:
    """Restore column names truncated by PostgreSQL identifier limit (63 bytes)."""
    try:
        orig_aliases = extract_select_aliases(model_sql)
        if len(orig_aliases) != len(columns):
            return columns
        restored = []
        for col, orig in zip(columns, orig_aliases):
            if orig and col != orig:
                trunc = orig.encode("utf-8")[:63].decode("utf-8", "ignore")
                if col == trunc or (len(col.encode("utf-8")) >= 60 and orig.startswith(col)):
                    restored.append(orig)
                    continue
            restored.append(col)
        return restored
    except Exception:
        return columns

@dataclass
class PostgresRunner:
    """Выполняет запросы модели. Соединение (psycopg 3) создаёт backend.

    Backend может передать своё соединение под ролью bi_reader:
        runner = PostgresRunner(conn)
    """
    conn: object
    schema: str = "raw"
    # Кэши общие для процесса: ключ включает сам источник (сервер:порт/база), чтобы
    # две внешние PostgreSQL с одинаковой public.sales не делили значения категорий.
    source: str | None = None
    max_rows: int = 200
    timeout_ms: int = 20000

    @classmethod
    def from_env(cls) -> "PostgresRunner":
        """Для локальной разработки: подключение по POSTGRES_* из .env, только чтение."""
        import psycopg
        try:
            from dotenv import load_dotenv
            load_dotenv()
        except ImportError:
            pass
        conn = psycopg.connect(
            host=os.getenv("POSTGRES_HOST", "127.0.0.1"),
            port=int(os.getenv("POSTGRES_PORT", "5432")),
            dbname=os.getenv("POSTGRES_DB", "hackathon"),
            user=os.getenv("POSTGRES_USER", "bi_reader"),
            password=os.getenv("POSTGRES_PASSWORD", ""),
            connect_timeout=10,
            autocommit=True,
            options="-c default_transaction_read_only=on -c statement_timeout=20000",
        )
        return cls(conn, schema=os.getenv("POSTGRES_SCHEMA", "raw"))

    @property
    def source_key(self) -> str:
        return self.source or _connection_key(self.conn)

    def _fetch(self, query: str, limit: int) -> tuple[list[str], list[tuple]]:
        with self.conn.transaction():
            cur = self.conn.cursor()
            cur.execute("SET TRANSACTION READ ONLY")
            cur.execute(f"SET LOCAL statement_timeout = {int(self.timeout_ms)}")
            cur.execute(query)
            columns = [d.name for d in cur.description]
            rows = cur.fetchmany(limit)
        return columns, rows

    def run(self, table: dict, model_sql: str) -> QueryResult:
        query = build_query(self.schema, table, model_sql, self.max_rows + 1)
        columns, rows = self._fetch(query, self.max_rows + 1)
        columns = restore_column_aliases(columns, model_sql)
        return QueryResult(columns, rows[:self.max_rows], truncated=len(rows) > self.max_rows)

    def category_values(self, table: dict, max_values: int = 15) -> dict[str, list[str]]:
        """Значения категорий (их немного), чтобы модель писала верные фильтры WHERE."""
        key = (self.source_key, self.schema, table["name"])
        if key in _VALUES_CACHE:
            return _VALUES_CACHE[key]
        values = {}
        source = f"{quote_ident(self.schema)}.{quote_ident(table['name'])}"
        for col in table.get("columns") or []:
            distinct = col.get("sample_distinct") or 0
            if col.get("inferred_type") != "category" or not 0 < distinct <= max_values:
                continue
            name = quote_ident(col["name"])
            _, rows = self._fetch(
                f"SELECT DISTINCT {name} FROM {source} "
                f"WHERE {name} IS NOT NULL AND btrim({name}) <> '' LIMIT {max_values + 1}",
                max_values + 1,
            )
            if len(rows) <= max_values:
                values[col["name"]] = sorted(str(r[0]) for r in rows)
        _VALUES_CACHE[key] = values
        return values

    def date_range(self, table: dict, column: str) -> tuple:
        """(минимум, максимум) колонки с датой — для «сентябрь» без года и «прошлый месяц»."""
        key = (self.source_key, self.schema, table["name"], column)
        if key not in _RANGE_CACHE:
            result = self.run(table, f'SELECT MIN({quote_ident(column)}), MAX({quote_ident(column)}) FROM t')
            _RANGE_CACHE[key] = tuple(result.rows[0]) if result.rows else (None, None)
        return _RANGE_CACHE[key]
