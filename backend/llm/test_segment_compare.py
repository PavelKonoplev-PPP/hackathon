"""Regression for #121: count + AVG + percent in a two-by-two segment comparison."""
import unittest
from decimal import Decimal

from backend.llm import compare, segment_compare as sc
from backend.llm.sql_guard import validate_sql


def col(name, kind):
    return {"name": name, "inferred_type": kind,
            "conversion": {"target_type": kind, "policy": "safe_complete"}}


DELIVERY = {
    "name": "data_04_Доставка",
    "columns": [
        col("Принято (МСК)", "timestamp"),
        col("Доставлено (МСК)", "timestamp"),
        col("Стоимость доставки, руб.", "decimal"),
        col("Вес, кг.", "decimal"),
        col("Объявленная стоимость, руб.", "decimal"),
        {"name": "Способ доставки", "inferred_type": "category"},
        {"name": "Перевозчик", "inferred_type": "category"},
        {"name": "Город получения", "inferred_type": "category"},
    ],
}
QUESTION = ("Сравни среднюю стоимость доставки по способу доставки и "
            "перевозчику за март и апрель 2025 года. По каждой комбинации "
            "покажи количество доставок, среднюю стоимость в каждом месяце "
            "и изменение в процентах.")


class SegmentCompareTests(unittest.TestCase):
    def plan(self):
        periods = compare.parse_periods(QUESTION)
        self.assertEqual([p.label for p in periods], ["март 2025", "апрель 2025"])
        kinds = {c["name"]: c.get("conversion", {}).get("target_type")
                 for c in DELIVERY["columns"]}
        date_col = compare.choose_date_column(DELIVERY, QUESTION, kinds)
        self.assertEqual(date_col, "Доставлено (МСК)")
        return sc.build(QUESTION, DELIVERY, periods, date_col)

    def test_exact_user_question_is_deterministic(self):
        plan = self.plan()
        self.assertIsNotNone(plan)
        self.assertEqual(plan.dims, ("Способ доставки", "Перевозчик"))
        self.assertIn('AVG("Стоимость доставки, руб.")', plan.sql)
        self.assertEqual(plan.sql.count('COUNT(*) FILTER'), 2)
        self.assertEqual(plan.sql.count('AVG('), 2)
        self.assertIn("100.0 *", plan.sql)
        self.assertIn("NULLIF(", plan.sql)
        self.assertIn("'2025-03-01'", plan.sql)
        self.assertIn("'2025-04-01'", plan.sql)
        self.assertIn("'2025-05-01'", plan.sql)
        self.assertIn('GROUP BY "Способ доставки", "Перевозчик"', plan.sql)
        self.assertNotIn("GROUP BY date_trunc", plan.sql)
        self.assertNotIn("SUM(", plan.sql)
        validate_sql(plan.sql, [c["name"] for c in DELIVERY["columns"]])

    def test_unrelated_average_or_one_dimension_does_not_trigger(self):
        self.assertIsNone(sc.build(
            "Сравни выручку за март и апрель 2025 года", DELIVERY,
            compare.parse_periods("март и апрель 2025"), "Доставлено (МСК)"))
        self.assertIsNone(sc.build(
            QUESTION, {**DELIVERY, "columns": DELIVERY["columns"][:-1][:-1]},
            compare.parse_periods(QUESTION), "Доставлено (МСК)"))

    def test_output_and_chart_only_comparable_ruble_measures(self):
        plan = self.plan()
        rows = [
            ("Курьер", "А", 3, Decimal("150"), 2, Decimal("225"), Decimal("50")),
            ("Почта", "Б", 1, Decimal("0"), 1, Decimal("100"), None),
        ]
        answer = plan.answer([], rows)
        self.assertIn("доставок 4 и 3", answer)
        self.assertIn("50.00%", answer)
        self.assertIn("AVG не учитывает пустую стоимость", answer)
        chart = plan.chart(rows)
        self.assertEqual(len(chart["series"]), 2)
        self.assertEqual(chart["series"][0]["data"], [150.0, 0.0])
        self.assertEqual(chart["series"][1]["data"], [225.0, 100.0])
        self.assertNotIn("Изменение", str(chart["series"]))

    def test_api_uses_verified_recipe_not_bad_llm_sql(self):
        from backend.llm.ask import _SESSIONS, answer_question
        from backend.llm.test_ask import FakeRunner, ScriptedClient

        _SESSIONS.clear()
        rows = [("Курьер", "А", 3, Decimal("150"), 2, Decimal("225"), Decimal("50"))]
        columns = ["Способ доставки", "Перевозчик", "Доставок: март 2025",
                   "Средняя стоимость: март 2025, руб.", "Доставок: апрель 2025",
                   "Средняя стоимость: апрель 2025, руб.", "Изменение, %"]
        runner = FakeRunner(rows=rows, columns=columns)
        model = ScriptedClient([])
        trace = {}
        response = answer_question(QUESTION, {"schema": "raw", "tables": [DELIVERY]},
                                   runner, client=model, trace=trace)
        self.assertEqual(trace.get("recipe"), "segment_compare")
        self.assertEqual(model.calls, [])
        self.assertEqual(len(runner.sqls), 1)
        self.assertIn("COUNT(*) FILTER", response["sql"])
        self.assertIn("AVG(", response["sql"])
        self.assertNotIn("GROUP BY date_trunc", response["sql"])
        self.assertIn("50.00%", response["answer"])
        self.assertEqual(response["table"]["columns"], columns)
        self.assertEqual(len(response["chart"]["series"]), 2)

    def test_failed_verified_comparison_does_not_fabricate(self):
        from backend.llm.ask import _SESSIONS, answer_question
        from backend.llm.test_ask import FakeRunner, ScriptedClient

        _SESSIONS.clear()
        runner = FakeRunner(fail_first=True)
        model = ScriptedClient([])
        response = answer_question(QUESTION, {"schema": "raw", "tables": [DELIVERY]},
                                   runner, client=model)
        self.assertIn("Не удалось надёжно рассчитать", response["answer"])
        self.assertEqual(model.calls, [])


if __name__ == "__main__":
    unittest.main()
