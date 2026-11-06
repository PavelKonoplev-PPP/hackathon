"""Готовые расчёты (#112 перцентили, #113 состояние на дату, #114 цепочки) — без модели и БД.

Совпадение чисел с эталонным SQL из TestCases.md проверено на PostgreSQL 16
(синтетические данные с той же схемой, включая попытки в произвольном порядке).
Здесь — что SQL строится по смыслу колонок, а не по названиям конкретных таблиц.
"""
import datetime as dt
import json
import unittest
from pathlib import Path

from backend.llm import recipes as rc

CATALOG = Path(__file__).resolve().parents[2] / "data" / "schema-catalog.json"
VALUES = CATALOG.with_name("category-values.json")


def col(name, kind=None, inferred=None):
    conv = {"target_type": kind, "policy": "safe_with_nulls"} if kind else {}
    return {"name": name, "inferred_type": inferred or kind or "category", "conversion": conv}


# Таблицы, которых нет в данных кейса: другие названия колонок и событий
TICKETS = {"name": "helpdesk", "columns": [
    col("Открыт", "timestamp"), col("Решён", "timestamp"), col("Команда"), col("Статус")]}
ORDERS = {"name": "shipments", "columns": [
    col("Плановая дата", "timestamp"), col("Вручено", "timestamp"), col("Курьер"),
    col("Цена, руб.", "decimal")]}
INVOICES = {"name": "invoices", "columns": [
    col("Выставлен", "timestamp"), col("Подписан", "timestamp"), col("Оплачен", "timestamp"),
    col("Аннулирован", "timestamp"), col("Подписанная сумма, руб.", "decimal"), col("Филиал"),
    col("Статус")]}
RUNS = {"name": "etl_runs", "columns": [
    col("Run chain", inferred="identifier"), col("Attempt", "integer"), col("Pipeline"),
    col("Status")]}


class PercentileTests(unittest.TestCase):
    def test_duration_with_period_group_and_min_count(self):
        q = ("У какой команды максимальный 90-й перцентиль времени решения среди тикетов, "
             "решённых в марте 2025? Исключи команды с менее чем 50 тикетами.")
        r = rc.build(q, TICKETS)
        self.assertEqual(r.kind, "percentile")
        self.assertIn('percentile_cont(0.9) WITHIN GROUP (ORDER BY EXTRACT(EPOCH FROM ("Решён" - "Открыт")) / 3600)', r.sql)
        self.assertIn('"Решён" >= \'2025-03-01\' AND "Решён" < \'2025-04-01\'', r.sql)
        self.assertIn('SELECT "Команда"', r.sql)
        self.assertIn("HAVING COUNT(*) >= 50", r.sql)
        self.assertIn("ORDER BY 3 DESC", r.sql)
        self.assertNotIn("AVG", r.sql.upper())

    def test_delay_is_never_negative(self):
        q = "Какой 95-й перцентиль задержки по курьерам среди врученных в мае 2025?"
        r = rc.build(q, ORDERS)
        self.assertIn('GREATEST(0, EXTRACT(EPOCH FROM ("Вручено" - "Плановая дата")) / 3600)', r.sql)
        self.assertIn('percentile_cont(0.95)', r.sql)
        self.assertIn("'2025-05-01'", r.sql)

    def test_median_and_units(self):
        r = rc.build("Медиана цены по курьерам", ORDERS)
        self.assertIn('percentile_cont(0.5) WITHIN GROUP (ORDER BY "Цена, руб.")', r.sql)
        r = rc.build("P90 времени решения в минутах", TICKETS)
        self.assertIn("/ 60)", r.sql)
        self.assertIn("перцентиль, мин", r.sql)

    def test_answer_names_top_group_and_method(self):
        q = "У какой команды максимальный 90-й перцентиль времени решения?"
        r = rc.build(q, TICKETS)
        text = r.answer(["Команда", "Наблюдений", "90-й перцентиль, ч"],
                        [("Склад", 120, 72.49), ("Касса", 300, 58.3)])
        self.assertIn("«Склад»: 72,49 ч (наблюдений: 120)", text)
        self.assertIn("Как посчитано", text)

    def test_no_metric_no_recipe(self):
        self.assertIsNone(rc.build("Какой 90-й перцентиль?", {"name": "x", "columns": [col("Город")]}))


class AsOfTests(unittest.TestCase):
    def test_state_on_date_with_negations(self):
        q = ("Сколько было подписано, но ещё не оплачено на конец 31 марта 2025 по филиалам, "
             "исключая аннулированные к этой дате счета?")
        r = rc.build(q, INVOICES)
        self.assertEqual(r.kind, "as_of")
        self.assertIn('"Подписан" < \'2025-04-01\'', r.sql)
        self.assertIn('("Оплачен" IS NULL OR "Оплачен" >= \'2025-04-01\')', r.sql)
        self.assertIn('("Аннулирован" IS NULL OR "Аннулирован" >= \'2025-04-01\')', r.sql)
        self.assertIn('SUM("Подписанная сумма, руб.")', r.sql)
        self.assertIn('SELECT "Филиал"', r.sql)
        self.assertNotIn('"Статус" =', r.sql)  # текущий статус на дату не годится

    def test_start_of_day_and_dotted_date(self):
        r = rc.build("Сколько счетов не оплачено на начало 01.04.2025?", INVOICES)
        self.assertIn('("Оплачен" IS NULL OR "Оплачен" >= \'2025-04-01\')', r.sql)
        self.assertIn('"Выставлен" < \'2025-04-01\'', r.sql)  # существовал к дате

    def test_parse_date(self):
        self.assertEqual(rc._asof_date("на конец 31 марта 2025"), dt.date(2025, 3, 31))
        self.assertEqual(rc._asof_date("по состоянию на 15.02.2026"), dt.date(2026, 2, 15))
        self.assertIsNone(rc._asof_date("Выручка за март 2025"))


class ChainsTests(unittest.TestCase):
    def test_chain_columns_found_by_meaning(self):
        q = "Для каждого pipeline посчитай долю цепочек, успешных только после повторных попыток"
        r = rc.build(q, RUNS, {"Status": ["failed", "success"], "Pipeline": ["a", "b"]})
        self.assertEqual(r.kind, "chains")
        self.assertIn('(array_agg("Status" ORDER BY "Attempt" DESC))[1] AS last_st', r.sql)
        self.assertIn("last_st = 'success' AND n_att > 1", r.sql)
        self.assertIn('GROUP BY g, ch', r.sql)
        text = r.answer(["Pipeline", "Цепочек", "Успешно после повторов", "Доля, %", "Среднее"],
                        [("a", 100, 6, 6.0, 2.3), ("b", 200, 8, 4.0, 2.1)])
        self.assertIn("от 4% до 6%", text)

    def test_without_success_value_no_recipe(self):
        self.assertIsNone(rc.build("доля цепочек после повторных попыток", RUNS,
                                   {"Status": ["failed", "cancelled"]}))


class AnomalyTests(unittest.TestCase):
    """#121: выбросы по Тьюки, с порогом, размером выборки и уровнем сущности."""
    SALES = {"name": "orders", "columns": [
        col("Номер чека", inferred="identifier"), col("Канал"), col("Количество, шт.", "integer"),
        col("Цена за единицу, руб.", "decimal")]}

    def test_entity_level_and_groups(self):
        r = rc.build("Найди аномальные значения суммы чеков по каналам и поясни критерий", self.SALES)
        self.assertEqual(r.kind, "anomaly")
        self.assertIn('"Номер чека" AS e, SUM("Количество, шт." * "Цена за единицу, руб.") AS v', r.sql)
        self.assertIn("percentile_cont(0.25)", r.sql)
        self.assertIn("1.5 * (s.q3 - s.q1)", r.sql)
        self.assertIn('d.g AS "Канал"', r.sql)
        text = r.answer(["Канал", "Наблюдений", "Q1", "Q3", "Верхняя", "Выбросов", "Максимум"],
                        [("Онлайн", 500, 10, 30, 60, 12, 900), ("Касса", 8, 5, 9, 15, 1, 40)])
        self.assertIn("Выбросов всего 12 из 500", text)
        self.assertIn("«Касса»", text)  # малую группу исключили из общего числа
        self.assertIn("Q1 − 1,5·IQR", text)

    def test_numeric_column_without_groups(self):
        r = rc.build("Есть ли выбросы в цене за единицу?", self.SALES)
        self.assertIn('SELECT "Цена за единицу, руб." AS v FROM t WHERE', r.sql)
        self.assertIn("CROSS JOIN s", r.sql)


    DATED_SALES = {"name": "sales", "columns": [
        col("Код операции", inferred="identifier"),
        col("Дата операции (МСК)", "timestamp"),
        col("Канал продажи"),
        col("Количество, шт.", "integer"),
        col("Цена до скидки за единицу, руб.", "decimal"),
        col("Скидка, %", "decimal"),
    ]}

    def test_large_outlier_is_upper_tail_and_respects_explicit_month(self):
        question = ("Найди аномально крупные суммы продаж по каналам продаж "
                    "за сентябрь 2025 года. Покажи критерий определения аномалии, "
                    "рассчитанный порог, количество аномальных операций и общее число операций "
                    "в каждом канале.")
        recipe = rc.anomaly_recipe(question, self.DATED_SALES)
        self.assertIsNotNone(recipe)
        self.assertIn('d.g AS "Канал продажи"', recipe.sql)
        self.assertIn('"Дата операции (МСК)" >= \'2025-09-01\'', recipe.sql)
        self.assertIn('"Дата операции (МСК)" < \'2025-10-01\'', recipe.sql)
        # Period must apply BEFORE per-channel quartiles and aggregation.
        self.assertIn('FROM t WHERE', recipe.sql)
        self.assertIn('AND ("Дата операции (МСК)"', recipe.sql)
        self.assertNotIn('OR d.v >', recipe.sql)
        self.assertNotIn('d.v < s.q1', recipe.sql)
        self.assertIn('d.v > s.q3 + 1.5 * (s.q3 - s.q1)', recipe.sql)
        self.assertIn('WHEN COUNT(*) >= 20', recipe.sql)
        self.assertIn('ELSE NULL END AS "Выбросов"', recipe.sql)
        self.assertIn('сентябрь 2025', recipe.description)
        self.assertIn('выше Q3 + 1,5·IQR', recipe.description)

    def test_lower_outliers_do_not_count_upper_tail(self):
        recipe = rc.anomaly_recipe("Покажи аномально низкие суммы по каналам за март 2025", self.DATED_SALES)
        self.assertIsNotNone(recipe)
        self.assertIn('d.v < s.q1 - 1.5 * (s.q3 - s.q1)', recipe.sql)
        self.assertNotIn('d.v > s.q3', recipe.sql)
        self.assertIn('"Нижняя граница"', recipe.sql)
        self.assertIn('ниже Q1 − 1,5·IQR', recipe.description)
        self.assertIn("'2025-03-01'", recipe.sql)
        self.assertIn("'2025-04-01'", recipe.sql)

    def test_unspecified_outlier_keeps_two_tails(self):
        recipe = rc.anomaly_recipe("Найди выбросы по каналам", self.DATED_SALES)
        # No metric in this question — recipe must not invent one.
        self.assertIsNone(recipe)
        recipe = rc.anomaly_recipe("Найди выбросы суммы по каналам", self.DATED_SALES)
        self.assertIn('d.v < s.q1', recipe.sql)
        self.assertIn('OR d.v > s.q3', recipe.sql)

    def test_entity_level_date_filter_applied_before_grouping(self):
        orders = {"name": "dated_orders", "columns": [
            col("Номер чека", inferred="identifier"),
            col("Создано", "date"),
            col("Канал"),
            col("Количество, шт.", "integer"),
            col("Цена за единицу, руб.", "decimal"),
        ]}
        recipe = rc.anomaly_recipe("Найди аномально крупные суммы чеков по каналам в марте 2025", orders)
        self.assertIsNotNone(recipe)
        self.assertIn('FROM t WHERE "Создано" >= \'2025-03-01\' '
                      'AND "Создано" < \'2025-04-01\' GROUP BY g, e', recipe.sql)

    def test_period_without_verified_date_column_is_never_dropped(self):
        self.assertIsNone(rc.anomaly_recipe(
            "Найди аномально крупные суммы чеков по каналам за сентябрь 2025",
            self.SALES
        ))

    def test_small_groups_do_not_generate_numeric_anomaly_counts(self):
        recipe = rc.anomaly_recipe("Найди аномально крупные суммы продаж по каналам", self.DATED_SALES)
        answer = recipe.answer(["Канал", "Наблюдений", "Q1", "Q3", "Верхняя граница", "Выбросов", "Максимум"],
                               [("Онлайн", 500, 10, 30, 60, 12, 900), ("Касса", 8, 5, 9, 15, None, 40)])
        self.assertIn("12 из 500", answer)
        self.assertIn("Исключены группы", answer)
        self.assertNotIn("12 из 508", answer)
        only_small = recipe.answer(["Канал", "Наблюдений", "Q1", "Q3", "Верхняя граница", "Выбросов", "Максимум"],
                                   [("Касса", 8, 5, 9, 15, None, 40)])
        self.assertIn("Недостаточно наблюдений", only_small)
        self.assertNotIn("Выбросов всего", only_small)



@unittest.skipUnless(CATALOG.is_file() and VALUES.is_file(), "нет каталога")
class RealCatalogTests(unittest.TestCase):
    """Вопросы 3–6 TestCases.md строят тот же расчёт, что эталон, и не трогают обычные вопросы."""

    def setUp(self):
        self.tables = {t["name"]: t for t in json.loads(CATALOG.read_text(encoding="utf-8"))["tables"]}
        self.values = json.loads(VALUES.read_text(encoding="utf-8"))

    def build(self, table, q):
        return rc.build(q, self.tables[table], self.values.get(table))

    def test_testcases_questions(self):
        r = self.build("data_02_Техническая_поддержка",
                       "У какой группы максимальный 90-й перцентиль времени решения среди обращений, "
                       "закрытых в марте 2025? Исключи группы с менее чем 100 закрытиями.")
        self.assertIn('"Группа поддержки"', r.sql)
        self.assertIn('("Закрыто (МСК)" - "Создано (МСК)")', r.sql)
        self.assertIn("HAVING COUNT(*) >= 100", r.sql)
        r = self.build("data_04_Доставка",
                       "У какого перевозчика максимальный 95-й перцентиль задержки среди доставленных "
                       "в марте 2025 отправлений? Доставку вовремя считай нулевой задержкой.")
        self.assertIn('GREATEST(0, EXTRACT(EPOCH FROM ("Доставлено (МСК)" - "Обещанный срок (МСК)"))', r.sql)
        r = self.build("data_07_Заявки_на_закупку",
                       "Сколько было согласовано, но ещё не оплачено на конец 31 марта 2025 по "
                       "подразделениям, исключая отменённые и отклонённые к этой дате заявки?")
        for part in ['"Согласована (МСК)" < \'2025-04-01\'', '"Оплачена (МСК)" IS NULL OR',
                     '"Отменена (МСК)" IS NULL OR', '"Отклонена (МСК)" IS NULL OR',
                     'SUM("Согласованная сумма, руб.")', 'SELECT "Подразделение"']:
            self.assertIn(part, r.sql)
        r = self.build("data_08_Фоновые_задания",
                       "Для каждого задания рассчитай долю цепочек, завершившихся успешно только после "
                       "повторных попыток, и среднее число попыток в таких цепочках.")
        self.assertIn('"Задание" AS g, "Код цепочки запусков" AS ch', r.sql)

    def test_no_false_triggers_on_regular_questions(self):
        from backend.llm.eval_cases import CASES
        from backend.llm.eval_holdout import HOLDOUT
        for case in CASES + HOLDOUT:
            if not case.get("table"):
                continue
            if case.get("recipe"):
                self.assertIsNotNone(self.build(case["table"], case["question"]), case["id"])
                continue
            for q in case.get("steps") or [case["question"]]:
                with self.subTest(case=case["id"], q=q):
                    self.assertIsNone(self.build(case["table"], q))


class AgentTests(unittest.TestCase):
    """Рецепт выполняется без модели; если SQL рецепта упал — обычный путь через модель."""

    def test_recipe_skips_model(self):
        from backend.llm.ask import _SESSIONS, answer_question
        from backend.llm.test_ask import FakeRunner, ScriptedClient
        _SESSIONS.clear()
        catalog = {"schema": "raw", "tables": [TICKETS]}
        runner = FakeRunner(rows=[("Склад", 120, 72.49)], columns=["Команда", "Наблюдений", "90-й перцентиль, ч"])
        client = ScriptedClient([])
        trace = {}
        result = answer_question("У какой команды максимальный 90-й перцентиль времени решения?",
                                 catalog, runner, client=client, trace=trace)
        self.assertEqual(trace["recipe"], "percentile")
        self.assertEqual(client.calls, [])
        self.assertIn("percentile_cont(0.9)", result["sql"])
        self.assertEqual(result["table_name"], "helpdesk")
        self.assertIn("«Склад»: 72,49 ч", result["answer"])


if __name__ == "__main__":
    unittest.main()
