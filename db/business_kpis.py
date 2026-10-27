"""BI-32: conservative KPI definitions from verified catalog columns.

No SQL execution, no mutation, no LLM assumptions. Definitions are proposed
until a business owner confirms the operational meaning and signs convention.
"""
from __future__ import annotations
import argparse
import json
from dataclasses import dataclass
from pathlib import Path

@dataclass(frozen=True)
class Definition:
    id: str
    table: str
    title: str
    unit: str
    required: tuple[str, ...]
    expression: str
    explanation: str

# All expressions refer to the typed CTE \`t\` exposed by PostgresRunner.
DEFINITIONS = (
    Definition("sales_rows", "data_01_Продажи", "Строки продаж", "rows",
        (), "COUNT(*)", "Количество записей, включая возвраты; не число заказов."),
    Definition("sales_operations", "data_01_Продажи", "Операции типа «Продажа»", "operations",
        ("Тип операции",), """COUNT(*) FILTER (WHERE "Тип операции" = 'Продажа')""",
        "Количество строк с типом операции «Продажа», не количество уникальных заказов."),
    Definition("sales_revenue", "data_01_Продажи", "Сумма с учётом скидки", "RUB",
        ("Количество, шт.", "Цена до скидки за единицу, руб.", "Скидка, %"),
        """SUM("Количество, шт." * "Цена до скидки за единицу, руб." * (1 - "Скидка, %" / 100.0))""",
        "Сумма строк по формуле количество × цена × (1−скидка/100). Знак возврата и бизнес-правила выручки требуют проверки."),
    Definition("support_tickets", "data_02_Техническая_поддержка", "Обращения в поддержку", "tickets",
        (), "COUNT(*)", "Число записей об обращениях; уникальность и переоткрытия отдельно не учтены."),
    Definition("support_avg_resolution_h", "data_02_Техническая_поддержка",
        "Среднее время от создания до закрытия", "hours",
        ("Создано (МСК)", "Закрыто (МСК)"),
        """AVG(EXTRACT(EPOCH FROM ("Закрыто (МСК)" - "Создано (МСК)")) / 3600.0)""",
        "Среднее по строкам с обеими датами; незакрытые обращения не входят в AVG."),
    Definition("ads_spend", "data_06_Рекламные_кампании", "Расходы на рекламу", "RUB",
        ("Расходы, руб.",), """SUM("Расходы, руб.")""", "Суммарное значение поля расходов."),
    Definition("ads_clicks", "data_06_Рекламные_кампании", "Клики", "clicks",
        ("Клики",), """SUM("Клики")""", "Суммарное количество кликов, не количество строк."),
    Definition("ads_ctr", "data_06_Рекламные_кампании", "CTR", "%",
        ("Клики", "Показы"),
        """100.0 * SUM("Клики") / NULLIF(SUM("Показы"), 0)""",
        "Отношение суммы кликов к сумме показов ×100; это не среднее построчных CTR."),
)

# Match the SQL types exposed by backend/llm/sql_runner.py, not just heuristic
# inferred_type labels: unsupported TEXT remains TEXT in the generated CTE.
OPERAND_KINDS = {
    "Тип операции": "text",
    "Количество, шт.": "numeric",
    "Цена до скидки за единицу, руб.": "numeric",
    "Скидка, %": "numeric",
    "Создано (МСК)": "timestamp",
    "Закрыто (МСК)": "timestamp",
    "Расходы, руб.": "numeric",
    "Клики": "numeric",
    "Показы": "numeric",
}
NUMERIC_STORAGE_TYPES = {
    "smallint", "integer", "bigint", "numeric", "decimal",
    "real", "double precision", "smallserial", "serial", "bigserial",
}
TEXT_STORAGE_TYPES = {"text", "character varying", "character", "varchar", "char"}
TIMESTAMP_STORAGE_TYPES = {
    "timestamp", "timestamp without time zone", "timestamp with time zone",
}


def _sql_operand_kind(column: dict) -> str:
    """Type the KPI expression actually sees inside the SQL runner's CTE."""
    conversion = column.get("conversion") or {}
    if isinstance(conversion, dict):
        target = conversion.get("target_type")
        if (target in {"integer", "decimal", "date", "timestamp"}
                and conversion.get("policy") in {"safe_complete", "safe_with_nulls"}):
            return "numeric" if target in {"integer", "decimal"} else target
    storage = str(column.get("postgres_type") or "").strip().lower()
    if storage in NUMERIC_STORAGE_TYPES:
        return "numeric"
    if storage in TIMESTAMP_STORAGE_TYPES:
        return "timestamp"
    if storage in TEXT_STORAGE_TYPES:
        return "text"
    if storage == "date":
        return "date"
    return "unknown"


def propose_kpis(catalog: dict) -> dict:
    if not isinstance(catalog, dict) or not isinstance(catalog.get("tables"), list):
        raise ValueError("Некорректный технический каталог")
    schema = catalog.get("schema")
    if not isinstance(schema, str) or not schema:
        raise ValueError("Не задана схема")
    tables = {t.get("name"): t for t in catalog["tables"] if isinstance(t, dict)}
    results = []
    for definition in DEFINITIONS:
        table = tables.get(definition.table)
        if table is None:
            continue
        cols = {c.get("name"): c for c in table.get("columns", []) if isinstance(c, dict)}
        missing = sorted(set(definition.required) - cols.keys())
        incompatible = []
        for name in definition.required:
            if name not in cols:
                continue
            expected = OPERAND_KINDS.get(name, "unknown")
            actual = _sql_operand_kind(cols[name])
            if actual != expected or expected == "unknown":
                incompatible.append(f"{name} (нужно {expected}, доступно {actual})")
        reasons = []
        if missing:
            reasons.append("Нет колонок: " + ", ".join(missing))
        if incompatible:
            reasons.append("Несовместимые типы колонок: " + ", ".join(incompatible))
        results.append({
            "id": definition.id, "schema": schema, "table": definition.table,
            "title": definition.title, "unit": definition.unit,
            "description": definition.explanation,
            "expression": definition.expression if not reasons else None,
            "required_columns": list(definition.required),
            "status": "proposed" if not reasons else "unavailable",
            "requires_business_approval": True,
            "warnings": reasons,
        })
    return {"version": 1, "schema": schema,
            "source_catalog_generated_at_utc": catalog.get("generated_at_utc"),
            "kpis": results, "summary": {
                "proposed": sum(k["status"] == "proposed" for k in results),
                "unavailable": sum(k["status"] == "unavailable" for k in results),
            }}


def main() -> int:
    parser = argparse.ArgumentParser(description="Предложения KPI по техническому каталогу")
    parser.add_argument("--catalog", type=Path, default=Path("data/schema-catalog.json"))
    parser.add_argument("--output", type=Path, default=Path("data/kpi-definitions.json"))
    args = parser.parse_args()
    try:
        data = propose_kpis(json.loads(args.catalog.read_text(encoding="utf-8")))
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(data["summary"], ensure_ascii=False))
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.exit(1, f"BI-32: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
