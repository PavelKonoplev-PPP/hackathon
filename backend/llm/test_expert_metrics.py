"""Unit tests for expert BI semantics; no real database or LLM needed."""
import unittest

from backend.llm.expert_metrics import (
    _end_of_day, _month_window, _percentile, check_expert_sql, expert_sql_hint,
)
from backend.llm.expert_recipes import expert_sql_recipe
from backend.llm.sql_guard import validate_sql

DRUG = ["ID_Аптеки", "ID_Лекарства", "Сколько_Купили", "Дата"]
SALES = ["Город", "Количество, шт.", "Цена до скидки за единицу, руб.",
         "Скидка, %", "Дата операции (МСК)"]
SUPPORT = ["Группа поддержки", "Создано (МСК)", "Закрыто (МСК)"]
DELIVERY = ["Перевозчик", "Обещанный срок (МСК)", "Доставлено (МСК)", "Статус"]
PURCHASE = ["Согласована (МСК)", "Оплачена (МСК)", "Отменена (МСК)",
            "Отклонена (МСК)", "Подразделение", "Статус", "Согласованная сумма, руб."]
JOBS = ["Задание", "Код цепочки запусков", "Номер попытки", "Статус",
        "Описание ошибки"]

Q_DRUG = (
    "Лекарственные препараты: Какое лекарство сильнее всего выросло в процентах "
    "по среднедневным продажам на аптеку в марте 2025 к февралю, "
    "включая дни без продаж? Пришли расчёт."
)
Q_SALES = (
    "Продажи: В каком городе сильнее всего выросла чистая выручка в процентах "
    "в марте 2025 к февралю с учётом количества, скидок и возвратов?"
)
Q_SUPPORT = (
    "Техподдержка: У какой группы максимальный 90-й перцентиль времени решения "
    "среди обращений, закрытых в марте 2025? Исключи группы с менее чем 100 закрытиями."
)
Q_DELIVERY = (
    "Доставка: У какого перевозчика максимальный 95-й перцентиль задержки "
    "среди доставленных в марте 2025 отправлений? "
    "Доставку вовремя считай нулевой задержкой."
)
Q_PURCHASE = (
    "Заявки на закупку: Сколько было согласовано, но ещё не оплачено на конец "
    "31 марта 2025 по подразделениям, исключая отменённые и отклонённые "
    "к этой дате заявки?"
)
Q_JOBS = (
    "Фоновые задания: Для каждого задания рассчитай долю цепочек, "
    "завершившихся успешно только после повторных попыток, "
    "и среднее число попыток в таких цепочках."
)

BAD_PURCHASE = """
SELECT "Подразделение", SUM("Согласованная сумма, руб.")
FROM t WHERE "Согласована (МСК)" <= '2025-03-31 23:59:59'::timestamp
AND "Оплачена (МСК)" IS NULL
AND "Отменена (МСК)" > '2025-03-31 23:59:59'::timestamp
AND "Статус" = 'Оплачена' GROUP BY "Подразделение"
"""

GOOD_PURCHASE = """
SELECT "Подразделение", COUNT(*), SUM("Согласованная сумма, руб.")
FROM t WHERE "Согласована (МСК)" < TIMESTAMP '2025-04-01'
AND ("Оплачена (МСК)" IS NULL OR "Оплачена (МСК)" >= TIMESTAMP '2025-04-01')
AND ("Отменена (МСК)" IS NULL OR "Отменена (МСК)" >= TIMESTAMP '2025-04-01')
AND ("Отклонена (МСК)" IS NULL OR "Отклонена (МСК)" >= TIMESTAMP '2025-04-01')
GROUP BY "Подразделение"
"""

BAD_JOBS = """
SELECT "Задание",
100.0 * COUNT(*) FILTER (WHERE "Код цепочки запусков" IS NOT NULL
AND "Статус" = 'Успешно') / COUNT(*),
AVG("Номер попытки") FROM t
WHERE "Описание ошибки" = 'Превышено допустимое число запросов'
GROUP BY "Задание"
"""

GOOD_JOBS = """
WITH chains AS (
SELECT "Задание" job, "Код цепочки запусков" chain,
       MAX("Номер попытки") attempts,
       (ARRAY_AGG("Статус" ORDER BY "Номер попытки" DESC))[1] final_status
FROM t GROUP BY "Задание", "Код цепочки запусков"
)
SELECT job, COUNT(*) chains_count,
       COUNT(*) FILTER (WHERE attempts > 1 AND final_status = 'Успешно') retry_ok,
       100.0 * COUNT(*) FILTER (WHERE attempts > 1 AND final_status = 'Успешно')
         / NULLIF(COUNT(*), 0) share_pct,
       AVG(attempts) FILTER (WHERE attempts > 1 AND final_status = 'Успешно') avg_attempts
FROM chains GROUP BY job
"""


class ExpertSqlHintsTests(unittest.TestCase):
    def test_russian_date_windows(self):
        self.assertEqual(_month_window("закрытых в марте 2025"), ("2025-03-01", "2025-04-01"))
        self.assertEqual(_month_window("за декабрь 2024"), ("2024-12-01", "2025-01-01"))
        self.assertEqual(_end_of_day("на конец 31 марта 2025"), "2025-04-01")
        self.assertIsNone(_end_of_day("31 февраля 2025"))

    def test_percentile_detection(self):
        self.assertEqual(_percentile(Q_SUPPORT.lower()), 90)
        self.assertEqual(_percentile(Q_DELIVERY.lower()), 95)
        self.assertEqual(_percentile("Покажи P90 по группам"), 90)
        self.assertIsNone(_percentile("Сколько 90% от суммы?"))

    def test_all_six_expert_questions_get_distinct_methods(self):
        cases = [
            (Q_DRUG, DRUG, "КАЛЕНДАРНЫХ"),
            (Q_SALES, SALES, "Отрицательное количество"),
            (Q_SUPPORT, SUPPORT, "PERCENTILE_CONT(0.9)"),
            (Q_DELIVERY, DELIVERY, "PERCENTILE_CONT(0.95)"),
            (Q_PURCHASE, PURCHASE, '"Оплачена (МСК)" IS NULL OR'),
            (Q_JOBS, JOBS, "ARRAY_AGG"),
        ]
        for question, columns, keyword in cases:
            with self.subTest(question=question):
                self.assertIn(keyword, expert_sql_hint(question, columns))

    def test_hints_not_fabricated_when_columns_absent(self):
        self.assertEqual(expert_sql_hint(Q_PURCHASE, ["Статус"]), "")
        self.assertEqual(expert_sql_hint(Q_JOBS, ["Номер попытки"]), "")
        self.assertEqual(expert_sql_hint(Q_DELIVERY, SUPPORT), "")
        self.assertEqual(expert_sql_hint("Сколько продаж?", SALES), "")


class ExpertSqlValidationTests(unittest.TestCase):
    def test_bad_support_percentile_rejected(self):
        bad = ('SELECT "Группа поддержки", MAX("Закрыто (МСК)") '
               'FROM t GROUP BY "Группа поддержки"')
        self.assertIn("PERCENTILE_CONT(0.9)", " ".join(
            check_expert_sql(bad, Q_SUPPORT, SUPPORT)
        ))
        good = ('SELECT "Группа поддержки", COUNT(*) AS cases, '
                'PERCENTILE_CONT(0.9) WITHIN GROUP '
                '(ORDER BY EXTRACT(EPOCH FROM ("Закрыто (МСК)" - "Создано (МСК)")) / 3600) '
                'FROM t GROUP BY "Группа поддержки" HAVING COUNT(*) >= 100')
        self.assertEqual(check_expert_sql(good, Q_SUPPORT, SUPPORT), [])

    def test_bad_delivery_percentile_and_negative_delay_rejected(self):
        sql = ('SELECT "Перевозчик", MAX(EXTRACT(EPOCH FROM '
               '("Доставлено (МСК)" - "Обещанный срок (МСК)"))) '
               'FROM t GROUP BY "Перевозчик"')
        errors = " ".join(check_expert_sql(sql, Q_DELIVERY, DELIVERY))
        self.assertIn("PERCENTILE_CONT(0.95)", errors)
        self.assertIn("GREATEST(0", errors)
        good = ('SELECT "Перевозчик", COUNT(*) AS deliveries, '
                'PERCENTILE_CONT(0.95) WITHIN GROUP '
                '(ORDER BY GREATEST(0, EXTRACT(EPOCH FROM '
                '("Доставлено (МСК)" - "Обещанный срок (МСК)")) / 3600)) '
                "FROM t WHERE \"Статус\" = 'Доставлено' "
                'GROUP BY "Перевозчик"')
        self.assertEqual(check_expert_sql(good, Q_DELIVERY, DELIVERY), [])

    def test_bad_purchase_rejected_even_when_valid_sql(self):
        errors = " ".join(check_expert_sql(BAD_PURCHASE, Q_PURCHASE, PURCHASE))
        self.assertIn("Не отбирай статус", errors)
        self.assertIn('"Оплачена (МСК)"', errors)
        self.assertIn('"Отклонена (МСК)"', errors)
        self.assertEqual(check_expert_sql(GOOD_PURCHASE, Q_PURCHASE, PURCHASE), [])

    def test_bad_retry_chain_rejected_even_when_valid_sql(self):
        errors = " ".join(check_expert_sql(BAD_JOBS, Q_JOBS, JOBS))
        self.assertIn("Код цепочки запусков", errors)
        self.assertIn("AVG", errors)
        self.assertIn("Описание ошибки", errors)
        self.assertEqual(check_expert_sql(GOOD_JOBS, Q_JOBS, JOBS), [])

    def test_actual_purchase_sql_wrong_midnight_is_rejected(self):
        reported = """
SELECT "Подразделение", COUNT(*), SUM("Согласованная сумма, руб.")
FROM t
WHERE "Согласована (МСК)" < TIMESTAMP '2025-03-31'
AND ("Оплачена (МСК)" IS NULL OR "Оплачена (МСК)" >= TIMESTAMP '2025-03-31')
AND ("Отменена (МСК)" IS NULL OR "Отменена (МСК)" >= TIMESTAMP '2025-03-31')
AND ("Отклонена (МСК)" IS NULL OR "Отклонена (МСК)" >= TIMESTAMP '2025-03-31')
GROUP BY "Подразделение"
"""
        problems = " ".join(check_expert_sql(reported, Q_PURCHASE, PURCHASE))
        self.assertIn("2025-04-01", problems)
        self.assertIn("начало последнего дня", problems)

    def test_actual_support_sql_missing_sample_size_is_rejected(self):
        reported = """
WITH s AS (
 SELECT "Группа поддержки",
 EXTRACT(EPOCH FROM ("Закрыто (МСК)" - "Создано (МСК)")) / 3600 AS "Часы_решения"
 FROM t WHERE "Закрыто (МСК)" >= '2025-03-01'
 AND "Закрыто (МСК)" < '2025-04-01'
)
SELECT "Группа поддержки",
 PERCENTILE_CONT(0.9) WITHIN GROUP (ORDER BY "Часы_решения")
 FROM s GROUP BY "Группа поддержки" HAVING COUNT(*) >= 100
"""
        errors = " ".join(check_expert_sql(reported, Q_SUPPORT, SUPPORT))
        self.assertIn("размер выборки", errors)

    def test_actual_delivery_sql_missing_count_and_status_is_rejected(self):
        reported = """
WITH delivery_delay AS (
 SELECT "Перевозчик",
 GREATEST(0, EXTRACT(EPOCH FROM (
 "Доставлено (МСК)" - "Обещанный срок (МСК)")) / 3600) AS "Задержка, часы"
 FROM t WHERE "Доставлено (МСК)" >= '2025-03-01'
 AND "Доставлено (МСК)" < '2025-04-01'
)
SELECT "Перевозчик",
 PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY "Задержка, часы")
 FROM delivery_delay GROUP BY "Перевозчик"
"""
        errors = " ".join(check_expert_sql(reported, Q_DELIVERY, DELIVERY))
        self.assertIn("размер выборки", errors)
        self.assertIn('Статус', errors)

    def test_deterministic_readonly_fallbacks(self):
        scenarios = [
            (Q_DRUG, DRUG, "COUNT(DISTINCT"),
            (Q_SALES, SALES, "growth_pct"),
            (Q_SUPPORT, SUPPORT, "HAVING COUNT(*) >= 100"),
            (Q_DELIVERY, DELIVERY, '"Статус" = \'Доставлено\''),
            (Q_PURCHASE, PURCHASE, "2025-04-01"),
            (Q_JOBS, JOBS, "ARRAY_AGG"),
        ]
        for question, columns, token in scenarios:
            with self.subTest(token=token):
                recipe = expert_sql_recipe(question, columns)
                self.assertIsNotNone(recipe)
                self.assertIn(token, recipe)
                self.assertEqual(validate_sql(recipe, columns), recipe)
                self.assertEqual(check_expert_sql(recipe, question, columns), [])

    def test_deterministic_fallback_requires_exact_domain_columns(self):
        self.assertIsNone(expert_sql_recipe(Q_DRUG, ["ID_Аптеки"]))
        self.assertIsNone(expert_sql_recipe(Q_SALES, ["Город"]))
        self.assertIsNone(expert_sql_recipe(Q_PURCHASE, ["Статус"]))
        self.assertIsNone(expert_sql_recipe("Сколько продаж?", SALES))

    def test_fallback_uses_calendar_days_not_sales_days(self):
        recipe = expert_sql_recipe(Q_DRUG, DRUG)
        self.assertIn("28.0", recipe)
        self.assertIn("31.0", recipe)
        self.assertIn("2025-02-01", recipe)
        self.assertIn("2025-04-01", recipe)

    def test_unrelated_queries_keep_existing_behaviour(self):
        other = 'SELECT "Подразделение", COUNT(*) FROM t GROUP BY "Подразделение"'
        self.assertEqual(check_expert_sql(other, "Сколько заявок по подразделениям?", PURCHASE), [])
        self.assertEqual(check_expert_sql(other, Q_PURCHASE, SALES), [])


class ExpertFallbackIntegrationTests(unittest.TestCase):
    """A real answer_question invocation with a refusing fake LLM."""

    def _run(self, fail_db=False):
        from decimal import Decimal

        from backend.llm.ask import answer_question
        from backend.llm.sql_runner import QueryResult

        class RepeatingBadClient:
            def __init__(self):
                self.calls = 0

            def chat(self, messages, **kwargs):
                self.calls += 1
                return 'SELECT "Задание", AVG("Номер попытки") FROM t GROUP BY "Задание"'

        class FakeReadOnlyRunner:
            def __init__(self):
                self.calls = []

            def category_values(self, table):
                return {}

            def run(self, table, sql):
                self.calls.append(sql)
                if fail_db:
                    raise RuntimeError("PostgreSQL query failed")
                return QueryResult(
                    columns=["job", "total_chains", "retry_success", "share_pct", "avg_attempts"],
                    rows=[("Очистка временных файлов", 11083, 581,
                           Decimal("5.24"), Decimal("2.32"))],
                )

        table = {
            "name": "data_08_Фоновые_задания",
            "columns": [
                {"name": column, "inferred_type": "integer" if column == "Номер попытки"
                 else "category"}
                for column in JOBS
            ],
        }
        runner = FakeReadOnlyRunner()
        client = RepeatingBadClient()
        trace = {}
        response = answer_question(
            Q_JOBS, {"schema": "raw", "tables": [table]}, runner,
            client=client, trace=trace,
            table_name="data_08_Фоновые_задания",
        )
        return response, runner, client, trace

    def test_llm_exhausted_then_fallback_executes_safe_sql(self):
        response, runner, client, trace = self._run()
        self.assertTrue(trace["expert_fallback"])
        self.assertEqual(len(trace["errors"]), 3)
        self.assertEqual(len(runner.calls), 1)
        self.assertIn("ARRAY_AGG", runner.calls[0])
        self.assertIn("SELECT", response["sql"])
        self.assertEqual(response["table_name"], "data_08_Фоновые_задания")
        self.assertEqual(len(response["table"]["rows"]), 1)
        self.assertGreaterEqual(client.calls, 3)

    def test_fallback_error_never_returns_fabricated_numbers(self):
        response, runner, client, trace = self._run(fail_db=True)
        self.assertFalse(response.get("table"))
        self.assertNotIn("11083", response["answer"])
        self.assertTrue(any("Ошибка проверенной формулы" in error
                            for error in trace["errors"]))

    def test_percentiles_are_not_additive(self):
        from backend.llm.ask import is_additive, template_answer
        self.assertFalse(is_additive("P90"))
        self.assertFalse(is_additive("95-й перцентиль задержки, часы"))
        text = template_answer(
            ["Перевозчик", "95-й перцентиль задержки, часы"],
            [("Первый", 22.5), ("Второй", 20.1)],
            False,
        )
        self.assertNotIn("Всего:", text)


if __name__ == "__main__":
    unittest.main()
