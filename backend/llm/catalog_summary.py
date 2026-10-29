"""Сжатие каталога данных в короткую сводку для LLM.

Каталог (db/catalog_db.py) большой и технический. Слабая модель в нём
путается, поэтому все факты считает код, а модели отдаём короткий текст.
Модуль не обращается к БД и не вызывает модель — его легко тестировать.
"""
from __future__ import annotations

# Понятные названия типов, которые определил профилировщик.
TYPE_LABELS = {
    "integer": "целое число",
    "decimal": "число",
    "date": "дата",
    "timestamp": "дата и время",
    "boolean": "да/нет",
    "identifier": "код/ID",
    "category": "категория",
    "text": "текст",
    "mixed": "смешанные значения",
    "unknown": "не определено",
}

POLICY_NOTES = {
    "safe_with_nulls": "есть пропуски",
    "blocked_invalid": "есть некорректные значения",
    "unverified": "не проверено",
}


def format_int(value: int | None) -> str:
    """5376000 -> '5 376 000' (неразрывный пробел не нужен модели)."""
    if value is None:
        return "неизвестно"
    return f"{value:,}".replace(",", " ")


def table_row_count(table: dict) -> int | None:
    """Точное число строк из полного аудита, иначе оценка PostgreSQL."""
    exact = []
    for col in table.get("columns") or []:
        if not isinstance(col, dict):
            continue
        conv = col.get("conversion")
        if isinstance(conv, dict):
            quality = conv.get("quality")
            if isinstance(quality, dict) and quality.get("total_rows") is not None:
                exact.append(quality["total_rows"])
    if exact:
        return max(exact)
    estimate = table.get("estimated_total_rows")
    if isinstance(estimate, int) and estimate >= 0:
        return estimate
    rows = table.get("row_count") or table.get("rows")
    return rows if isinstance(rows, int) and rows >= 0 else None


def display_table_name(name: str) -> str:
    """'data_01_Продажи' -> 'Продажи' (префикс добавляет загрузчик)."""
    if name.startswith("data_"):
        parts = name.split("_", 2)
        if len(parts) == 3 and parts[1].isdigit():
            return parts[2].replace("_", " ")
    return name


def match_table_name(table: dict, target: str) -> bool:
    """Check if table matches target string by raw name, display name, prefix, or code."""
    raw = (table.get("name") or "").strip()
    disp = display_table_name(raw).strip()
    q = target.strip()
    if not q or not raw:
        return False
    if raw == q or disp == q:
        return True
    q_lower = q.lower()
    raw_lower = raw.lower()
    disp_lower = disp.lower()
    if raw_lower == q_lower or disp_lower == q_lower:
        return True
    if raw_lower.replace("_", " ") == q_lower.replace("_", " "):
        return True
    if disp_lower.replace("_", " ") == q_lower.replace("_", " "):
        return True
    if q_lower.startswith("raw."):
        q_no_schema = q_lower[4:]
        if raw_lower == q_no_schema or disp_lower == q_no_schema:
            return True
    if raw_lower.startswith("data_") and raw_lower[5:] == q_lower:
        return True
    parts = raw_lower.split("_", 2)
    if len(parts) >= 2 and parts[1].isdigit():
        code = f"data_{parts[1]}"
        num = parts[1]
        num_int = str(int(parts[1]))
        if q_lower in (code, num, num_int, f"data_{num_int}"):
            return True
        if q_lower.startswith(code + "_"):
            return True
    return False


def _column_note(col: dict) -> str:
    kind = col.get("inferred_type") or "unknown"
    label = TYPE_LABELS.get(kind, kind)
    notes = []
    conversion = col.get("conversion") or {}
    policy = conversion.get("policy")
    quality = conversion.get("quality") or {}
    total = quality.get("total_rows")
    if policy in POLICY_NOTES:
        note = POLICY_NOTES[policy]
        if policy == "safe_with_nulls" and total:
            share = quality.get("missing_rows", 0) / total
            note = f"пропуски {share:.0%}" if share >= 0.01 else "пропуски <1%"
        elif policy == "blocked_invalid" and total:
            note = f"некорректных значений {format_int(quality.get('invalid_rows'))}"
        notes.append(note)
    return f"{label}, {', '.join(notes)}" if notes else label


def table_block(table: dict, max_columns: int = 40) -> str:
    """Описание одной таблицы: название, строки, колонки с типами."""
    columns = table.get("columns") or []
    lines = [
        f"Таблица «{display_table_name(table.get('name', ''))}» "
        f"(в базе: {table.get('name', '')}), строк: {format_int(table_row_count(table))}, "
        f"колонок: {len(columns)}"
    ]
    for col in columns[:max_columns]:
        if isinstance(col, dict):
            lines.append(f"  - {col.get('name', '')}: {_column_note(col)}")
    if len(columns) > max_columns:
        lines.append(f"  - ... и ещё {len(columns) - max_columns} колонок")
    return "\n".join(lines)


def summarize_catalog(catalog: dict, relationships: dict | None = None,
                      max_columns: int = 40) -> str:
    """Текстовая сводка каталога: таблицы, строки, колонки с типами, связи."""
    tables = catalog.get("tables") or []
    total_rows = sum(table_row_count(t) or 0 for t in tables)
    lines = [f"Всего таблиц: {len(tables)}.",
             f"Всего строк во всех таблицах: {format_int(total_rows)}."]
    for table in tables:
        lines.append("")
        lines.append(table_block(table, max_columns))

    if relationships:
        lines.append("")
        lines.append(relationships_section(relationships))
    return "\n".join(lines)


def overview_table(catalog: dict) -> dict:
    """Таблица для фронтенда (поле `table` в docs/api-contract.md)."""
    rows = []
    for table in catalog.get("tables") or []:
        if not isinstance(table, dict):
            continue
        columns = table.get("columns") or []
        kinds = [c.get("inferred_type") for c in columns if isinstance(c, dict)]
        rows.append([
            display_table_name(table.get("name", "")),
            format_int(table_row_count(table)),
            str(len(columns)),
            str(sum(k in ("integer", "decimal") for k in kinds)),
            str(sum(k in ("date", "timestamp") for k in kinds)),
        ])
    return {
        "columns": ["Таблица", "Строк", "Колонок", "Числовых", "С датами"],
        "rows": rows,
    }


def missing_share(col: dict) -> float | None:
    quality = (col.get("conversion") or {}).get("quality") or {}
    total = quality.get("total_rows")
    if not total:
        return None
    return quality.get("missing_rows", 0) / total


def quality_section(catalog: dict, threshold: float = 0.01) -> str:
    """Раздел «Качество данных» — только факты из полного аудита, без модели."""
    lines = []
    invalid_total = 0
    for table in catalog.get("tables") or []:
        notes = []
        for col in table.get("columns") or []:
            quality = (col.get("conversion") or {}).get("quality") or {}
            invalid_total += quality.get("invalid_rows", 0) or 0
            share = missing_share(col)
            if share is not None and share >= threshold:
                notes.append(f"{col['name']} — {share:.0%}")
        if notes:
            lines.append(f"- «{display_table_name(table['name'])}»: пустые значения в полях "
                         + "; ".join(notes))
    header = ["Качество данных (проверено на всех строках):"]
    if invalid_total == 0:
        header.append("- Числа и даты везде в корректном формате, ошибок формата нет.")
    else:
        header.append(f"- Значений в некорректном формате: {format_int(invalid_total)}.")
    if lines:
        header.extend(lines)
        header.append("Пустое поле не всегда ошибка: например, дата закрытия или возврата "
                      "заполняется только когда событие произошло.")
    else:
        header.append("- Заметных пропусков нет.")
    return "\n".join(header)


def relationships_section(relationships: dict) -> str:
    """Связи из каталога связей: внутри таблиц и между таблицами."""
    raw_joins = (
        relationships.get("join_candidates")
        or relationships.get("relationships")
        or []
    )
    if not isinstance(raw_joins, list):
        raw_joins = []
    joins = [
        j for j in raw_joins
        if isinstance(j, dict)
        and (j.get("technical_reference_verified") or j.get("status") == "technical_reference_verified")
        and isinstance(j.get("source"), dict)
        and isinstance(j.get("target"), dict)
    ]
    inner = [j for j in joins if j["source"].get("table") == j["target"].get("table")]
    cross = [j for j in joins if j["source"].get("table") != j["target"].get("table")]
    lines = []
    if inner:
        lines.append("Ссылки внутри одной таблицы (строка ссылается на другую строку):")
        for j in inner:
            src, dst = j["source"], j["target"]
            lines.append(f"  - «{display_table_name(src.get('table', ''))}»: "
                         f"{src.get('column', '')} -> {dst.get('column', '')}")
        lines.append("")
    if cross:
        lines.append("Возможные связи между таблицами (технически совпадают коды, "
                     "бизнес-смысл не подтверждён):")
        for j in cross:
            src, dst = j["source"], j["target"]
            lines.append(f"  - «{display_table_name(src.get('table', ''))}».{src.get('column', '')} -> "
                         f"«{display_table_name(dst.get('table', ''))}».{dst.get('column', '')}")
    else:
        lines.append("Подтверждённых связей между разными таблицами не найдено: "
                     "таблицы стоит анализировать по отдельности.")
    return "\n".join(lines)
