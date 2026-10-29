"""Проверка SQL, который написала модель, до выполнения в базе.

Без внешних зависимостей. Это первый рубеж защиты; дальше запрос всё равно
выполняется в read-only транзакции, с таймаутом и под ролью только для чтения.

Модель пишет запрос к одной подготовленной таблице `t` (см. sql_runner.py).
"""
from __future__ import annotations

import re


class SQLRejected(ValueError):
    """Запрос отклонён. Текст ошибки понятен модели — его можно вернуть ей на исправление."""


FORBIDDEN_KEYWORDS = {
    "insert", "update", "delete", "merge", "upsert", "drop", "alter", "create",
    "truncate", "grant", "revoke", "copy", "call", "do", "execute", "prepare",
    "deallocate", "set", "reset", "lock", "vacuum", "analyze", "cluster",
    "reindex", "refresh", "comment", "listen", "notify", "unlisten", "load",
    "discard", "begin", "commit", "rollback", "savepoint", "release", "into",
    "security", "import", "checkpoint",
}

# Служебные и потенциально опасные функции PostgreSQL.
FORBIDDEN_FUNCTIONS = re.compile(
    r"\b(pg_\w+|dblink\w*|lo_\w+|set_config|current_setting|query_to_xml\w*|"
    r"table_to_xml\w*|txid_\w+|inet_\w+|has_\w+_privilege)\s*\(",
    re.IGNORECASE,
)

_SQL_BLOCK = re.compile(r"```(?:sql)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)
_SQL_START = re.compile(r"\b(with|select)\b", re.IGNORECASE)


def extract_sql(text: str) -> str:
    """Достаёт SQL из ответа модели: из блока ```sql``` или с первого SELECT/WITH."""
    match = _SQL_BLOCK.search(text)
    if match:
        return match.group(1).strip()
    start = _SQL_START.search(text)
    if not start:
        raise SQLRejected("В ответе нет SQL-запроса. Ответь только SQL-запросом SELECT.")
    return text[start.start():].strip()


def _mask(sql: str) -> tuple[str, list[str]]:
    """Убирает комментарии и строки, заменяет "идентификаторы" на заглушки.

    Возвращает текст, в котором можно безопасно искать ключевые слова,
    и список идентификаторов в двойных кавычках.
    """
    out = []
    identifiers = []
    i, n = 0, len(sql)
    while i < n:
        ch = sql[i]
        if sql.startswith("--", i):
            end = sql.find("\n", i)
            i = n if end == -1 else end
            out.append(" ")
        elif sql.startswith("/*", i):
            end = sql.find("*/", i + 2)
            if end == -1:
                raise SQLRejected("Незакрытый комментарий /* в запросе.")
            i = end + 2
            out.append(" ")
        elif ch == "'":
            j = i + 1
            while True:
                j = sql.find("'", j)
                if j == -1:
                    raise SQLRejected("Незакрытая кавычка ' в запросе.")
                if sql.startswith("''", j):
                    j += 2
                    continue
                break
            out.append(" '' ")
            i = j + 1
        elif ch == '"':
            j = i + 1
            name = []
            while True:
                k = sql.find('"', j)
                if k == -1:
                    raise SQLRejected('Незакрытая кавычка " в названии колонки.')
                name.append(sql[j:k])
                if sql.startswith('""', k):
                    name.append('"')
                    j = k + 2
                    continue
                break
            identifiers.append("".join(name))
            out.append(f" __id{len(identifiers) - 1}__ ")
            i = k + 1
        elif ch == "$":
            raise SQLRejected("Символ $ в запросе не разрешён.")
        else:
            out.append(ch)
            i += 1
    return "".join(out), identifiers


def fix_column_case(sql: str, columns: list[str]) -> str:
    """"канал продажи" -> "Канал продажи": маленькие модели путают регистр."""
    by_lower = {c.lower(): c for c in columns}

    def repl(m):
        name = m.group(1)
        fixed = by_lower.get(name.lower())
        return f'"{fixed}"' if fixed and fixed != name else m.group(0)

    return re.sub(r'"([^"]+)"', repl, sql)


_QUOTED_OR_STRING = re.compile(r'("(?:[^"]|"")*"|\'(?:[^\']|\'\')*\')')


def _mask_function_keywords(text: str) -> str:
    """Mask 'FROM' keyword inside SQL functions like EXTRACT, SUBSTRING, TRIM so they
    are not mistaken for table source clauses in SELECT ... FROM table."""
    pattern = re.compile(r"\b(extract|substring|trim)\s*\(", re.IGNORECASE)
    res = list(text)
    for m in pattern.finditer(text):
        start = m.end()
        depth = 1
        i = start
        while i < len(text) and depth > 0:
            if text[i] == "(":
                depth += 1
            elif text[i] == ")":
                depth -= 1
            i += 1
        end = i - 1 if depth == 0 else len(text)
        fn_body = "".join(res[start:end])
        fn_body_masked = re.sub(r"\bfrom\b", "    ", fn_body, flags=re.IGNORECASE)
        res[start:end] = list(fn_body_masked)
    return "".join(res)


def quote_bare_columns(sql: str, columns: list[str]) -> str:
    """ID_Аптеки -> "ID_Аптеки": модель забывает кавычки у названий без пробелов,
    а PostgreSQL без кавычек приводит их к нижнему регистру и не находит."""
    simple = [c for c in columns if re.fullmatch(r"[\w]+", c)]
    if not simple:
        return sql
    pattern = re.compile(r"(?<![\w.\"])(" + "|".join(map(re.escape, sorted(simple, key=len, reverse=True)))
                         + r")(?![\w\"])", re.IGNORECASE)
    by_lower = {c.lower(): c for c in simple}
    parts = _QUOTED_OR_STRING.split(sql)
    for i in range(0, len(parts), 2):  # чётные части — вне кавычек и строк
        parts[i] = pattern.sub(lambda m: f'"{by_lower[m.group(1).lower()]}"', parts[i])
    return "".join(parts)


def validate_sql(sql: str, columns: list[str] | None = None,
                 table_alias: str = "t") -> str:
    """Проверяет запрос и возвращает его без завершающей ';'.

    columns — названия колонок таблицы `t`: если модель обратилась к
    несуществующей колонке, ошибка подскажет, какие есть.
    """
    sql = sql.strip()
    while sql.endswith(";"):
        sql = sql[:-1].rstrip()
    if not sql:
        raise SQLRejected("Пустой запрос.")

    if columns:
        sql = fix_column_case(quote_bare_columns(sql, columns), columns)
    masked, identifiers = _mask(sql)
    if ";" in masked:
        raise SQLRejected("Разрешён только один запрос, без ';' внутри.")

    words = re.findall(r"[a-z_][a-z0-9_]*", masked.lower())
    if not words or words[0] not in ("select", "with"):
        raise SQLRejected("Запрос должен начинаться с SELECT (или WITH).")
    bad = sorted(FORBIDDEN_KEYWORDS.intersection(words))
    if bad:
        raise SQLRejected(f"Запрещённые команды: {', '.join(bad).upper()}. Разрешён только SELECT.")
    if FORBIDDEN_FUNCTIONS.search(masked):
        raise SQLRejected("Служебные функции PostgreSQL использовать нельзя.")

    # Источник данных: только t или подзапросы/CTE, которые модель объявила сама.
    ctes = {m.lower() for m in re.findall(r"(?:\bwith|,)\s*([a-z_][a-z0-9_]*)\s+as\s*\(",
                                         masked, re.IGNORECASE)}
    allowed_sources = {table_alias, *ctes}
    sources_masked = _mask_function_keywords(masked)
    for source in re.findall(r"\b(?:from|join)\s+([^\s(),]+)", sources_masked, re.IGNORECASE):
        if source.lower() not in allowed_sources:
            shown = source
            m = re.fullmatch(r"__id(\d+)__", source)
            if m:
                shown = f'"{identifiers[int(m.group(1))]}"'
            raise SQLRejected(f"Нельзя читать из {shown}: данные доступны только в таблице {table_alias}.")

    if columns is not None:
        aliases = {identifiers[int(i)] for i in
                   re.findall(r"\bas\s+__id(\d+)__", masked, re.IGNORECASE)}
        known = set(columns) | aliases
        unknown = [name for name in identifiers if name not in known]
        if unknown:
            raise SQLRejected(
                f'Колонки "{unknown[0]}" нет в таблице {table_alias}. Используй названия точно '
                f"из списка: " + ", ".join(f'"{c}"' for c in columns)
            )
    return sql
