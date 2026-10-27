"""Бизнес-словарь для агента (BI-37 / #62).

Словарь строит db/business_glossary.py в data/business-glossary.json: для каждого поля —
описание, синонимы, единица и статус. Агент доверяет только записям со статусом
«approved» (их подтвердил человек командой `approve`); автоматические предложения
(«proposed») и «needs_review» не используются — это догадки по названию поля, а маленькой
модели они только мешают.

Что даёт подтверждённая запись:
- в описании таблицы для модели у колонки появляется смысл и синонимы;
- синонимы помогают выбрать таблицу и колонку («куда доставили» → «Город получения»);
- смысловая проверка SQL считает колонку названной, если в вопросе есть её синоним.

Словарь перечитывается сам, когда файл меняется: после `approve` + `build` перезапуск
бэкенда не нужен. Путь можно задать переменной BUSINESS_GLOSSARY_PATH.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from .semantics import _contains_phrase, words

DEFAULT_PATH = Path(__file__).resolve().parents[2] / "data" / "business-glossary.json"
TRUSTED = "approved"

_CACHE: dict[str, tuple[float, dict]] = {}


def glossary_path() -> Path:
    return Path(os.getenv("BUSINESS_GLOSSARY_PATH") or DEFAULT_PATH)


def load(path: Path | None = None) -> dict:
    """{(schema, table, column): запись} — только подтверждённые. Нет файла — пусто."""
    path = Path(path or glossary_path())
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return {}
    key = str(path)
    cached = _CACHE.get(key)
    if cached and cached[0] == mtime:
        return cached[1]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        entries = data.get("entries") or []
    except (OSError, ValueError, AttributeError):
        return {}  # испорченный словарь не должен ломать ответы агента
    trusted = {}
    for e in entries:
        if not isinstance(e, dict) or e.get("status") != TRUSTED:
            continue
        if not all(isinstance(e.get(k), str) for k in ("schema", "table", "column")):
            continue
        trusted[(e["schema"], e["table"], e["column"])] = {
            "description": str(e.get("description") or "").strip(),
            "synonyms": [str(s).strip() for s in e.get("synonyms") or [] if str(s).strip()],
            "unit": e.get("unit"),
        }
    _CACHE[key] = (mtime, trusted)
    return trusted


def apply(catalog: dict, trusted: dict | None = None) -> dict:
    """Копия каталога, где у колонок с подтверждённым определением есть ключ "glossary".
    Исходный каталог не меняется. Нет подтверждённых записей — возвращается как есть."""
    trusted = load() if trusted is None else trusted
    if not trusted:
        return catalog
    schema = catalog.get("schema")
    tables = []
    changed = False
    for t in catalog.get("tables") or []:
        columns = []
        for c in t.get("columns") or []:
            entry = trusted.get((schema, t.get("name"), c.get("name")))
            if entry:
                c = {**c, "glossary": entry}
                changed = True
            columns.append(c)
        tables.append({**t, "columns": columns})
    return {**catalog, "tables": tables} if changed else catalog


def synonyms(col: dict) -> list[str]:
    return list((col.get("glossary") or {}).get("synonyms") or [])


def column_words(col: dict) -> set[str]:
    """Слова названия колонки и её подтверждённых синонимов."""
    result = set(words(col["name"]))
    for s in synonyms(col):
        result |= words(s)
    return result


def aliases(table: dict) -> dict[str, list[str]]:
    """{колонка: синонимы} — для смысловой проверки SQL."""
    return {c["name"]: synonyms(c) for c in table.get("columns") or [] if synonyms(c)}


def note(col: dict) -> str:
    """Хвост строки колонки в промпте: «; смысл: …; синонимы: …»."""
    entry = col.get("glossary")
    if not entry:
        return ""
    text = ""
    if entry.get("description"):
        text += f"; смысл: {entry['description']}"
    if entry.get("synonyms"):
        text += "; в вопросах называют: " + ", ".join(f"«{s}»" for s in entry["synonyms"])
    return text



def trusted_term(question: str) -> str | None:
    """Синоним из подтверждённого словаря, названный в вопросе («CSAT»), или None.
    Если команда сама определила термин в словаре, отказ «такой метрики нет» не нужен
    (semantics.detect_out_of_scope — его вызывают и агент, и бэкенд)."""
    for entry in load().values():
        for s in entry.get("synonyms") or []:
            if _contains_phrase(question, s):
                return s
    return None
