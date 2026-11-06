"""Scenario #118: validated lag correlation with actual calendar date pairing."""
import unittest

from backend.llm import lag_analysis as la
from backend.llm import recipes as rc
from backend.llm.sql_guard import validate_sql


def column(name, kind):
    return {"name": name, "inferred_type": kind,
            "conversion": {"target_type": kind, "policy": "safe_complete"}}


TABLE = {"name": "ad_daily", "columns": [
    column("Дата", "date"),
    column("Клики", "integer"),
    column("Выручка, руб.", "decimal"),
    column("Показы", "integer"),
    {"name": "Площадка", "inferred_type": "category"},
]}


class LagAnalysisTests(unittest.TestCase):
    def test_explicit_lag_range_and_metric_order(self):
        prompt = ("Проверь временной лаг между кликами и выручкой "
                  "от -7 до +7 дней по дневным данным рекламных кампаний.")
        recipe = rc.build(prompt, TABLE)
        self.assertIsNotNone(recipe)
        self.assertEqual(recipe.kind, "lag")
        sql = validate_sql(recipe.sql, [c["name"] for c in TABLE["columns"]])
        self.assertIn('SUM("Клики")::double precision AS x', sql)
        self.assertIn('SUM("Выручка, руб.")::double precision AS y', sql)
        self.assertIn("date_trunc('day', \"Дата\")::date AS day", sql)
        self.assertIn("SELECT -7::integer AS lag_days", sql)
        self.assertIn("SELECT 7::integer", sql)
        self.assertIn("b.day = a.day + shifts.lag_days", sql)
        self.assertIn("CORR(x, y)", sql)
        self.assertIn("WHERE n >= 5 AND r IS NOT NULL", sql)
        self.assertNotIn("generate_series", sql)
        self.assertNotIn("lag(x", sql.lower())
        self.assertIn("реально совпадающим календарным датам", recipe.description)

    def test_exact_safari_request_metrics_are_not_polluted_by_output_words(self):
        from backend.llm.ask import answer_question
        from backend.llm.test_ask import FakeRunner, ScriptedClient

        # Real user wording: output instructions refer to a coefficient, to
        # the count of paired days, and to the display action. None of those
        # should become an extra measured series.
        question = (
            "В таблице «Рекламные кампании» проверь временной лаг между "
            "кликами и выручкой от −7 до +7 дней. Покажи коэффициент "
            "корреляции Пирсона, количество сопоставленных дней и "
            "направление временного сдвига."
        )
        cluttered = {**TABLE, "name": "data_06_Рекламные_кампании",
                     "columns": [*TABLE["columns"],
                                 column("Количество, шт.", "integer"),
                                 column("Коэффициент", "decimal")]}
        self.assertEqual(la._metrics(question, cluttered),
                         ("Клики", "Выручка, руб."))
        plan = la.build_lag_plan(question, cluttered)
        self.assertIsNotNone(plan)
        self.assertIn("сдвигов от -7 до +7 дней", plan.description)
        runner = FakeRunner(rows=[(3, 28, 0.88)],
                            columns=["Лаг, дней", "Пар наблюдений", "Корреляция r"])
        model = ScriptedClient([])
        response = answer_question(question,
                                   {"schema": "raw", "tables": [cluttered]},
                                   runner, client=model)
        self.assertIn("«Клики» предшествует «Выручка, руб.»", response["answer"])
        self.assertIn("CORR(x, y)", response["sql"])
        self.assertEqual(model.calls, [])

    def test_explicit_quoted_table_beats_fuzzy_table_ranking(self):
        from backend.llm.ask import choose_table

        ads = {**TABLE, "name": "data_06_Рекламные_кампании"}
        sales = {"name": "data_01_Продажи", "columns": [
            column("Клики", "integer"), column("Выручка, руб.", "decimal"),
            column("Дата", "date"), {"name": "Канал", "inferred_type": "category"},
        ]}
        question = ("В таблице «Рекламные кампании» проверь временной лаг "
                    "между кликами и выручкой от -7 до +7 дней. "
                    "Покажи коэффициент корреляции.")
        selected, candidates = choose_table(question, [sales, ads], {}, "raw")
        self.assertEqual(selected["name"], "data_06_Рекламные_кампании")
        self.assertEqual(candidates, [])

    def test_extra_metric_inside_pair_clarifies_instead_of_guessing(self):
        prompt = ("Проверь лаг между кликами и выручкой и показами "
                  "от -7 до +7 дней. Покажи коэффициент.")
        self.assertIsNone(la.build_lag_plan(prompt, TABLE))

    def test_requested_source_direction(self):
        plan = la.build_lag_plan(
            "Посмотри, опережают ли клики выручку с лагом 5 дней", TABLE)
        self.assertIsNotNone(plan)
        self.assertIn("SELECT -5::integer", plan.sql)
        self.assertIn("SELECT 5::integer", plan.sql)
        ans = plan.answer([], [(3, 20, 0.94), (0, 23, 0.12)])
        self.assertIn("«Клики» предшествует «Выручка, руб.» на 3 дн.", ans)
        self.assertIn("пар наблюдений: 20", ans)
        self.assertIn("r = +0.9400", ans)
        self.assertIn("не подтверждает причинно-следственную", ans)

    def test_negative_and_zero_shift_are_distinct(self):
        plan = la.build_lag_plan("Проверь лаг между кликами и выручкой ±3 дня", TABLE)
        self.assertIn("«Выручка, руб.» предшествует «Клики»", plan.answer([], [(-2, 15, -0.8)]))
        self.assertIn("Сдвига между календарными днями нет", plan.answer([], [(0, 15, 0.6)]))

    def test_period_in_source_and_not_after_date_pairing(self):
        plan = la.build_lag_plan("Есть ли лаг между кликами и выручкой в марте 2025 от -2 до +2 дней?", TABLE)
        self.assertIn('"Дата" >= \'2025-03-01\' AND "Дата" < \'2025-04-01\'', plan.sql)
        self.assertLess(plan.sql.index("2025-03-01"), plan.sql.index("GROUP BY 1"))
        self.assertIn("сдвигов от -2 до +2 дней", plan.description)

    def test_does_not_guess_columns(self):
        missing = {**TABLE, "columns": TABLE["columns"][:2]}
        self.assertIsNone(la.build_lag_plan("Какой лаг между кликами и выручкой?", missing))
        self.assertIsNone(la.build_lag_plan("Какая корреляция между кликами и выручкой?", missing))
        self.assertIsNone(la.build_lag_plan("Лаг рекламы по дням", TABLE))
        self.assertIsNone(la.build_lag_plan("Лаг между кликами и выручкой и показами", TABLE))

    def test_no_date_no_lag(self):
        t = {**TABLE, "columns": [c for c in TABLE["columns"] if c["name"] != "Дата"]}
        self.assertIsNone(la.build_lag_plan("Лаг между кликами и выручкой", t))

    def test_rejects_unbounded_search(self):
        self.assertIsNone(la.build_lag_plan("Лаг между кликами и выручкой ±100 дней", TABLE))
        self.assertIsNone(la.build_lag_plan("Лаг между кликами и выручкой от 7 до -7 дней", TABLE))

    def test_unspecified_range_is_bounded(self):
        plan = la.build_lag_plan("Есть ли лаг между кликами и выручкой?", TABLE)
        self.assertIn("сдвигов от -7 до +7 дней", plan.description)

    def test_insufficient_pairs_reports_limit_not_causality(self):
        plan = la.build_lag_plan("Проверь лаг между кликами и выручкой", TABLE)
        self.assertIn("не нашлось сдвигов", plan.answer([], []))
        self.assertIn("Пропущенные даты не дополнялись", plan.answer([], []))

    def test_regular_correlation_still_uses_existing_path(self):
        self.assertFalse(la.is_lag_request("Есть ли корреляция между кликами и выручкой?"))
        self.assertIsNone(rc.build("Есть ли корреляция между кликами и выручкой?", TABLE))


class LagAgentIntegrationTests(unittest.TestCase):
    def setUp(self):
        from backend.llm.ask import _SESSIONS
        _SESSIONS.clear()

    def test_agent_uses_lag_recipe_without_llm_guessing(self):
        from backend.llm.ask import answer_question
        from backend.llm.test_ask import FakeRunner, ScriptedClient

        runner = FakeRunner(rows=[(3, 28, 1.0), (0, 31, 0.12)],
                            columns=["Лаг, дней", "Пар наблюдений", "Корреляция r"])
        model = ScriptedClient([])
        trace = {}
        response = answer_question(
            "Проверь временной лаг между кликами и выручкой от -7 до +7 дней",
            {"schema": "raw", "tables": [TABLE]}, runner,
            client=model, trace=trace)
        self.assertEqual(trace.get("recipe"), "lag")
        self.assertEqual(trace.get("attempts"), 0)
        self.assertEqual(model.calls, [])
        self.assertIn("на 3 дн.", response["answer"])
        self.assertIn("CORR(x, y)", response["sql"])
        self.assertEqual(response["table"]["rows"][0][0], "3")

    def test_agent_does_not_fabricate_lag_without_date(self):
        from backend.llm.ask import answer_question
        from backend.llm.test_ask import FakeRunner, ScriptedClient

        no_date = {**TABLE, "columns": [
            c for c in TABLE["columns"] if c["name"] != "Дата"]}
        model = ScriptedClient([])
        runner = FakeRunner()
        response = answer_question(
            "Проверь временной лаг между кликами и выручкой от -7 до +7 дней",
            {"schema": "raw", "tables": [no_date]}, runner, client=model)
        self.assertIn("проверенное поле даты", response["answer"])
        self.assertNotIn("r =", response["answer"])
        self.assertEqual(model.calls, [])
        self.assertEqual(runner.sqls, [])

    def test_failed_verified_lag_does_not_fall_back_to_llm(self):
        from backend.llm.ask import answer_question
        from backend.llm.test_ask import FakeRunner, ScriptedClient

        model = ScriptedClient([])
        runner = FakeRunner(fail_first=True)
        response = answer_question(
            "Проверь временной лаг между кликами и выручкой от -7 до +7 дней",
            {"schema": "raw", "tables": [TABLE]}, runner, client=model)
        self.assertIn("Не удалось выполнить проверенный расчёт", response["answer"])
        self.assertEqual(model.calls, [])


if __name__ == "__main__":
    unittest.main()
