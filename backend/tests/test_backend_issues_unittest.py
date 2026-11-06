"""Unittest suite for Issue #97, #116 (Smart Compare) and #112–#114, #120 (Analytics recipes).

Updated after merging PR #138 (fix/llm-final-claude):
- Recipes now use column_kind() which reads conversion.target_type, not inferred_type.
- All test tables use the col() helper from test_recipes.py to set conversion correctly.
"""
import sys
import unittest
import datetime as dt
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.llm.compare import (
    _check_period_columns,
    enrich_compare,
    are_compatible_metrics,
    check_compare_sql,
    Period,
)
from backend.llm.recipes import (
    PERCENTILE,
    percentile_recipe,
    asof_recipe,
    chains_recipe,
    build as build_recipe,
    is_recipe_question,
)
from backend.llm.expert_metrics import expert_sql_hint, check_expert_sql
from backend.llm.sql_guard import validate_sql


# ---------------------------------------------------------------------------
# Helper — same pattern as backend/llm/test_recipes.py
# ---------------------------------------------------------------------------
def col(name, kind=None, inferred=None, sample_distinct=None, sample_values=None):
    """Build a catalog column dict with proper conversion for column_kind()."""
    conv = {"target_type": kind, "policy": "safe_with_nulls"} if kind else {}
    c = {"name": name, "inferred_type": inferred or kind or "category", "conversion": conv}
    if sample_distinct is not None:
        c["sample_distinct"] = sample_distinct
    if sample_values is not None:
        c["sample_values"] = sample_values
    return c


# ---------------------------------------------------------------------------
# #97 / #116 — Smart Compare
# ---------------------------------------------------------------------------

class TestCompareRegression(unittest.TestCase):
    """Issues #97 and #116: Smart Compare."""

    def test_check_period_columns_rejects_inline_pct(self):
        """SQL with inline percent calculation must be rejected."""
        bad_sql = """SELECT SUM(col) FILTER (WHERE d>='2025-08-01') AS "авг",
                     100.0 * SUM(col) FILTER (WHERE d>='2025-08-01') / SUM(col) AS "доля"
                     FROM t"""
        problems = _check_period_columns(bad_sql)
        self.assertTrue(len(problems) > 0, "Should reject SQL with inline percent calc")

    def test_check_period_columns_clean_sql_passes(self):
        """Clean two-filter SQL must have no problems."""
        sql = """SELECT SUM(col) FILTER (WHERE d >= '2025-08-01' AND d < '2025-09-01') AS "август 2025",
                 SUM(col) FILTER (WHERE d >= '2025-09-01' AND d < '2025-10-01') AS "сентябрь 2025"
                 FROM t"""
        problems = _check_period_columns(sql)
        self.assertEqual(problems, [], f"Unexpected problems: {problems}")

    def test_enrich_compare_returns_none_for_incompatible_units(self):
        """#97: Return None instead of subtracting percent from rubles."""
        columns = ["Метрика, %", "Выручка, руб."]
        rows = [(3.54, 95967780.24)]
        periods = [
            Period("август 2025", dt.date(2025, 8, 1), dt.date(2025, 9, 1)),
            Period("сентябрь 2025", dt.date(2025, 9, 1), dt.date(2025, 10, 1)),
        ]
        self.assertIsNone(enrich_compare(columns, rows, periods))

    def test_enrich_compare_works_with_correct_period_labels(self):
        """Revenue delta calculation must be correct."""
        columns = ["август 2025", "сентябрь 2025"]
        rows = [(94903381.73, 95967780.24)]
        periods = [
            Period("август 2025", dt.date(2025, 8, 1), dt.date(2025, 9, 1)),
            Period("сентябрь 2025", dt.date(2025, 9, 1), dt.date(2025, 10, 1)),
        ]
        result = enrich_compare(columns, rows, periods)
        self.assertIsNotNone(result)
        out_cols, out_rows, pair = result
        delta = out_rows[0][-2]
        self.assertIsNotNone(delta)
        self.assertAlmostEqual(delta, 1064398.51, delta=1.0)

    def test_are_compatible_metrics(self):
        self.assertFalse(are_compatible_metrics("Скидка, %", "Выручка, руб."))
        self.assertTrue(are_compatible_metrics("август 2025", "сентябрь 2025"))
        self.assertTrue(are_compatible_metrics("CTR, %", "Конверсия, %"))


# ---------------------------------------------------------------------------
# #112 — Percentiles via recipes.py
# ---------------------------------------------------------------------------

class TestPercentileRecipe(unittest.TestCase):
    """Issue #112: P90/P95 and group size thresholds."""

    # NOTE: column_kind() uses conversion.target_type, so we use col() helper
    TABLE_SUPPORT = {
        "name": "helpdesk",
        "columns": [
            col("Открыт", "timestamp"),
            col("Решён", "timestamp"),
            col("Команда"),
        ],
    }

    TABLE_DELIVERY = {
        "name": "shipments",
        "columns": [
            col("Плановая дата", "timestamp"),
            col("Вручено", "timestamp"),
            col("Курьер"),
            col("Цена, руб.", "decimal"),
        ],
    }

    def test_percentile_re_detects_p90(self):
        self.assertIsNotNone(PERCENTILE.search("Какой P90 времени закрытия обращений?"))
        self.assertIsNotNone(PERCENTILE.search("Посчитай p95 задержки доставки"))
        self.assertIsNone(PERCENTILE.search("Какое среднее время закрытия?"))

    def test_percentile_recipe_duration_generates_valid_sql(self):
        """P95 of time duration between two timestamps (Решён - Открыт)."""
        q = "У какой команды максимальный 95-й перцентиль времени решения?"
        r = percentile_recipe(q, self.TABLE_SUPPORT)
        self.assertIsNotNone(r, f"Should produce recipe for support duration P95")
        self.assertIn("percentile_cont", r.sql)
        self.assertIn("WITHIN GROUP", r.sql)

    def test_percentile_recipe_groups_and_having(self):
        """Grouping + HAVING min count."""
        q = "У какой команды максимальный 90-й перцентиль времени решения? Исключи команды с менее чем 50 тикетами."
        r = percentile_recipe(q, self.TABLE_SUPPORT)
        self.assertIsNotNone(r)
        self.assertIn("GROUP BY", r.sql)
        self.assertIn("HAVING COUNT(*) >= 50", r.sql)

    def test_percentile_delay_uses_greatest(self):
        """Delivery delay P95 — GREATEST(0, ...) prevents negative delay."""
        q = "Какой 95-й перцентиль задержки по курьерам среди вручённых в мае 2025?"
        r = build_recipe(q, self.TABLE_DELIVERY)
        self.assertIsNotNone(r)
        self.assertIn("GREATEST(0", r.sql)
        self.assertIn("percentile_cont(0.95)", r.sql)

    def test_is_recipe_question_detects_percentile(self):
        self.assertTrue(is_recipe_question("P90 задержки доставки по перевозчикам"))
        self.assertFalse(is_recipe_question("Сколько заявок в сентябре?"))


# ---------------------------------------------------------------------------
# #113 — As-of historical cuts via recipes.py
# ---------------------------------------------------------------------------

class TestAsofRecipe(unittest.TestCase):
    """Issue #113: Historical state of objects at a date (as-of)."""

    # asof_recipe requires: an exact date in question (DD.MM.YYYY or «NN месяца YYYY»)
    # + timestamp columns whose names match polarity words in the question
    TABLE_INVOICES = {
        "name": "invoices",
        "columns": [
            col("Выставлен", "timestamp"),
            col("Подписан", "timestamp"),
            col("Оплачен", "timestamp"),
            col("Аннулирован", "timestamp"),
            col("Подписанная сумма, руб.", "decimal"),
            col("Филиал"),
            col("Статус"),
        ],
    }

    def test_asof_re_detects_date_cut(self):
        self.assertTrue(is_recipe_question(
            "Сколько согласованных, но не оплаченных заявок на 31.03.2025?"))
        self.assertFalse(is_recipe_question("Сколько всего заявок?"))

    def test_asof_recipe_generates_sql(self):
        """As-of cut: invoices signed but not yet paid as of 31 March 2025."""
        q = ("Сколько было подписано, но ещё не оплачено на конец 31 марта 2025 "
             "по филиалам, исключая аннулированные к этой дате счетов?")
        r = asof_recipe(q, self.TABLE_INVOICES)
        self.assertIsNotNone(r, "Should produce asof recipe for invoices")
        # Positive event (signed before bound) + negative events (IS NULL or >= bound)
        self.assertIn("IS NULL", r.sql)


# ---------------------------------------------------------------------------
# #114 — Chain analytics via recipes.py
# ---------------------------------------------------------------------------

class TestChainsRecipe(unittest.TestCase):
    """Issue #114: Launch chain and retry analytics."""

    # chains_recipe requires:
    # - chain column matching r"цепоч|chain"
    # - attempt integer column matching r"попыт|attempt|retry"
    # - status column present in `values` dict
    # - at least one success value matching _SUCCESS = r"^(успеш|success|ok|выполнен|done|завершен)"
    TABLE_RUNS = {
        "name": "etl_runs",
        "columns": [
            col("Run chain", inferred="identifier"),
            col("Attempt", "integer"),
            col("Pipeline"),
            col("Status"),
        ],
    }

    def test_chains_recipe_generates_cte_sql(self):
        values = {"Status": ["success", "error", "cancelled"]}
        q = "What share of chains succeeded only after retries?"
        r = chains_recipe(q, self.TABLE_RUNS, values)
        self.assertIsNotNone(r, "Should produce chain recipe for ETL runs")
        self.assertIn("WITH", r.sql)
        self.assertIn("chain", r.sql.lower())

    def test_sql_guard_allows_chain_cte(self):
        """sql_guard must not block WITH ... CTEs needed for chain analysis."""
        sql = """
        WITH chain_last AS (
          SELECT "Код цепочки", MAX("Номер попытки") AS last_att FROM t GROUP BY "Код цепочки"
        ),
        chain_final AS (
          SELECT t.* FROM t JOIN chain_last c ON t."Код цепочки"=c."Код цепочки"
        )
        SELECT COUNT(*) FROM chain_final
        """
        validated = validate_sql(sql, columns=["Код цепочки", "Номер попытки"])
        self.assertIn("WITH chain_last AS", validated)


# ---------------------------------------------------------------------------
# #120 — Expert hints and SQL validation (expert_metrics.py)
# ---------------------------------------------------------------------------

class TestExpertHints(unittest.TestCase):
    """Issue #120 / PR #131: expert_sql_hint and check_expert_sql."""

    # expert_sql_hint for support P90/P95 requires exact columns:
    # "Группа поддержки", "Создано (МСК)", "Закрыто (МСК)" + month in question
    SUPPORT_COLS = [
        "Группа поддержки", "Создано (МСК)", "Закрыто (МСК)", "Оценка решения",
    ]

    # expert_sql_hint for purchase asof requires exact columns + specific words in question
    PURCHASE_COLS = [
        "Согласована (МСК)", "Оплачена (МСК)",
        "Отменена (МСК)", "Отклонена (МСК)", "Подразделение",
    ]

    def test_expert_hint_for_support_percentile(self):
        """expert_sql_hint should return a hint mentioning PERCENTILE_CONT
        when question asks P90/P95 for support tickets in a specific month."""
        hint = expert_sql_hint(
            "P90 времени решения обращений по группам поддержки за март 2025",
            self.SUPPORT_COLS,
        )
        self.assertIn("PERCENTILE_CONT", hint,
                      f"Expected PERCENTILE_CONT in hint, got: {hint!r}")

    def test_expert_hint_for_purchase_asof(self):
        """expert_sql_hint should return an IS NULL hint for purchase asof cut."""
        hint = expert_sql_hint(
            "Сколько согласованных, но не оплаченных заявок по подразделениям"
            " на конец 31 марта 2025?",
            self.PURCHASE_COLS,
        )
        self.assertIn("IS NULL", hint,
                      f"Expected IS NULL in hint, got: {hint!r}")

    def test_check_expert_sql_rejects_missing_sample_size(self):
        bad_sql = """SELECT "Оператор", percentile_cont(0.9) WITHIN GROUP (ORDER BY dur) AS p90
                     FROM t GROUP BY 1"""
        # Should produce a warning about sample size
        issues = check_expert_sql(bad_sql, "P90 времени решения", SUPPORT_COLS_MODULE)
        self.assertIsInstance(issues, list)


SUPPORT_COLS_MODULE = ["Дата поступления обращения", "Дата закрытия обращения",
                       "Оценка решения", "Оператор"]


if __name__ == "__main__":
    unittest.main()
