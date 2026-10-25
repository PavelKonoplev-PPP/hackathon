"""Тесты разбора вопроса и смысловой проверки SQL (без модели и БД)."""
import unittest

from backend.llm.semantics import Intent, adjust_sql, check_sql, counted_noun, mentions

VALUES = {"Канал продажи": ["Интернет-магазин", "Мобильное приложение", "Розничный магазин"],
          "Статус": ["Ошибка", "Отменено", "Успешно"]}


class IntentTests(unittest.TestCase):
    def test_parse(self):
        i = Intent.parse("Сколько разных товаров в каждой категории?")
        self.assertTrue(i.is_count and i.wants_distinct and i.has_unit)
        self.assertFalse(i.wants_top)
        i = Intent.parse("Топ-5 городов по выручке")
        self.assertTrue(i.wants_top)
        self.assertEqual(i.top_n, 5)
        self.assertEqual(Intent.parse("Какой канал приносит больше всего выручки?").top_n, None)
        self.assertTrue(Intent.parse("Какой канал приносит больше всего выручки?").wants_top)
        self.assertEqual(Intent.parse("Выручка по месяцам").time_grain, "month")
        self.assertEqual(Intent.parse("Сколько заказов по годам").time_grain, "year")
        self.assertEqual(Intent.parse("Сколько продаж в каждом месяце?").time_grain, "month")
        self.assertIsNone(Intent.parse("А теперь только за сентябрь 2025 года").time_grain)
        self.assertFalse(Intent.parse("Сколько продаж без карты?").has_unit)

    def test_groups(self):
        self.assertTrue(Intent.parse("Средняя скидка по категориям товаров").wants_groups)
        self.assertTrue(Intent.parse("Сколько заявок в каждом статусе?").wants_groups)
        self.assertFalse(Intent.parse("Сколько всего кликов по рекламе?").wants_groups)

    def test_counted_noun(self):
        self.assertEqual(counted_noun("Сколько всего было продаж без карты?"), "продаж")
        self.assertIsNone(counted_noun("Какая выручка?"))

    def test_mentions(self):
        self.assertTrue(mentions("Сколько строк через розницу?", "Розничный магазин"))
        self.assertTrue(mentions("продажи через мобильное приложение", "Мобильное приложение"))
        self.assertFalse(mentions("Выручка по каналам", "Интернет-магазин"))


class ViewTests(unittest.TestCase):
    def test_views(self):
        from backend.llm.semantics import is_pure_view_request, requested_view
        self.assertEqual(requested_view("Покажи то же самое кругом"), "pie")
        self.assertTrue(is_pure_view_request("Покажи то же самое кругом"))
        self.assertTrue(is_pure_view_request("сделай столбцами, пожалуйста"))
        self.assertTrue(is_pure_view_request("в виде таблицы"))
        self.assertEqual(requested_view("А теперь по месяцам линией"), "line")
        self.assertFalse(is_pure_view_request("А теперь по месяцам линией"))
        self.assertIsNone(requested_view("покажи таблицу продаж"))


class AdjustTests(unittest.TestCase):
    def test_limit_removed_without_top(self):
        sql = 'SELECT "Месяц", COUNT(*) FROM t GROUP BY 1 ORDER BY 2 DESC LIMIT 10'
        self.assertNotIn("LIMIT", adjust_sql(sql, Intent.parse("Сколько продаж по месяцам?")))

    def test_top_n_enforced(self):
        sql = 'SELECT "Город", SUM(x) FROM t GROUP BY 1 ORDER BY 2 DESC LIMIT 10'
        self.assertTrue(adjust_sql(sql, Intent.parse("Топ-5 городов")).endswith("LIMIT 5"))
        sql = 'SELECT "Город", SUM(x) FROM t GROUP BY 1 ORDER BY 2 DESC'
        self.assertTrue(adjust_sql(sql, Intent.parse("Первые 3 города")).endswith("LIMIT 3"))

    def test_top1_widened(self):
        sql = 'SELECT "Канал", SUM(x) FROM t GROUP BY 1 ORDER BY 2 DESC LIMIT 1'
        self.assertTrue(adjust_sql(sql, Intent.parse("Какой канал самый прибыльный?"))
                        .endswith("LIMIT 10"))

    def test_plain_limit_without_group_kept(self):
        sql = 'SELECT COUNT(*) FROM t LIMIT 1'
        self.assertEqual(adjust_sql(sql, Intent.parse("Сколько записей?")), sql)


class CheckTests(unittest.TestCase):
    def check(self, sql, question, strict=True):
        return check_sql(sql, question, Intent.parse(question), VALUES, strict)

    def test_count_needs_aggregate(self):
        self.assertTrue(self.check("SELECT * FROM t", "Сколько продаж без карты?"))
        self.assertFalse(self.check("SELECT COUNT(*) FROM t", "Сколько продаж без карты?"))

    def test_distinct_required(self):
        problems = self.check('SELECT COUNT("Товар") FROM t', "Сколько разных товаров?")
        self.assertIn("DISTINCT", problems[0])
        self.assertFalse(self.check('SELECT COUNT(DISTINCT "Товар") FROM t', "Сколько разных товаров?"))

    def test_sum_for_numeric_noun(self):
        q = "Сколько всего было кликов по рекламе?"
        bad = check_sql("SELECT COUNT(*) FROM t", q, Intent.parse(q), {}, numeric_columns=["Клики", "Показы"])
        self.assertIn('SUM("Клики")', bad[0])
        self.assertFalse(check_sql('SELECT SUM("Клики") FROM t', q, Intent.parse(q), {},
                                   numeric_columns=["Клики"]))

    def test_group_by_required(self):
        q = "Какая средняя скидка по категориям товаров?"
        self.assertIn("GROUP BY", self.check('SELECT AVG("Скидка, %") FROM t', q)[0])

    def test_missing_filter_prefers_exact_phrase(self):
        values = {"Уровень программы лояльности": ["Базовый", "Без карты лояльности"],
                  "Способ оплаты": ["Банковская карта", "Наличные"]}
        q = "Сколько строк продаж без карты?"
        problems = check_sql("SELECT COUNT(*) FROM t", q, Intent.parse(q), values,
                             columns=list(values))
        self.assertIn("'Без карты лояльности'", problems[0])
        self.assertNotIn("Банковская", problems[0])

    def test_must_read_t(self):
        self.assertTrue(self.check("SELECT 'нет данных'", "Какая погода?"))

    def test_time_grouping_required(self):
        self.assertTrue(self.check('SELECT COUNT(*) FROM t', "Сколько продаж по месяцам?"))

    def test_filter_values(self):
        q = "Сколько строк через розницу?"
        self.assertFalse(self.check('SELECT COUNT(*) FROM t WHERE "Канал продажи" = \'Розничный магазин\'', q))
        bad = self.check('SELECT COUNT(*) FROM t WHERE "Канал продажи" = \'Розница\'', q)
        self.assertIn("нет значения 'Розница'", bad[0])
        extra = self.check('SELECT COUNT(*) FROM t WHERE "Канал продажи" IN '
                           '(\'Розничный магазин\', \'Интернет-магазин\')', q)
        self.assertEqual(len(extra), 1)
        self.assertIn("'Интернет-магазин' не упоминается", extra[0])
        self.assertFalse(self.check('SELECT COUNT(*) FROM t WHERE "Канал продажи" IN '
                                    '(\'Розничный магазин\', \'Интернет-магазин\')', q, strict=False))

    def test_like_and_unchecked_columns(self):
        q = "Сколько заданий с ошибкой?"
        self.assertFalse(self.check('SELECT COUNT(*) FROM t WHERE "Статус" ILIKE \'%ошиб%\'', q))
        self.assertTrue(self.check('SELECT COUNT(*) FROM t WHERE "Статус" LIKE \'%фонов%\'', q))
        # у «Города» значений не знаем — не придираемся
        self.assertFalse(self.check('SELECT COUNT(*) FROM t WHERE "Город" = \'Москва\'', "Сколько в Москве?"))


class EvalRunnerSmokeTest(unittest.TestCase):
    def test_run_eval_modes(self):
        from backend.llm.eval import run_eval
        from backend.llm.test_ask import CATALOG, FakeRunner, ScriptedClient

        cases = [
            dict(id="v", table="data_01_Продажи", mode="value", expected=5819,
                 question="Сколько было операций возврата?"),
            dict(id="r", table=None, mode="refuse", question="Какая погода?"),
            dict(id="c", table="data_01_Продажи", mode="clarify", clarify="unit",
                 question="Сколько продаж через интернет-магазин?"),
        ]
        client = ScriptedClient(['```sql\nSELECT COUNT(*) FROM t WHERE "Тип операции" = \'Возврат\'\n```',
                                 "x"])
        report = run_eval(cases, CATALOG, FakeRunner(rows=[(5819,)], columns=["N"]),
                          client=client, log=lambda *_: None)
        self.assertEqual([r["ok"] for r in report], [True, True, True], report)


class UnsupportedMetricsTests(unittest.TestCase):
    """Тесты распознавания отсутствующих в датасете сущностей и метрик (BI-05, Issue #33)."""

    def test_nps_detected(self):
        from backend.llm.semantics import detect_out_of_scope
        for phrase in [
            "Какой средний NPS у наших клиентов?",
            "Посчитай индекс лояльности",
            "Какой показатель NPS за прошлый месяц?",
            "Оценка готовности рекомендовать",
        ]:
            info = detect_out_of_scope(phrase)
            self.assertIsNotNone(info, f"Failed on {phrase}")
            self.assertEqual(info.topic, "nps")
            self.assertIn("NPS", info.explanation)
            self.assertGreaterEqual(len(info.suggestions), 2)
            d = info.to_dict()
            self.assertIn("NPS", d["answer"])
            self.assertIsNone(d["sql"])
            self.assertIsNone(d["table"])
            self.assertIsNone(d["chart"])

    def test_roi_detected(self):
        from backend.llm.semantics import detect_out_of_scope
        for phrase in [
            "Посчитай ROI рекламных кампаний",
            "Какой ROMI по площадкам?",
            "Какая окупаемость инвестиций в рекламу?",
            "Покажи ROAS",
        ]:
            info = detect_out_of_scope(phrase)
            self.assertIsNotNone(info, f"Failed on {phrase}")
            self.assertEqual(info.topic, "roi")
            self.assertIn("ROI", info.explanation)
            self.assertGreaterEqual(len(info.suggestions), 2)

    def test_ltv_detected(self):
        from backend.llm.semantics import detect_out_of_scope
        for phrase in [
            "Покажи LTV покупателей",
            "Какой средний CLV клиента?",
            "Какая пожизненная ценность клиента?",
        ]:
            info = detect_out_of_scope(phrase)
            self.assertIsNotNone(info, f"Failed on {phrase}")
            self.assertEqual(info.topic, "ltv")
            self.assertIn("LTV", info.explanation)
            self.assertGreaterEqual(len(info.suggestions), 2)

    def test_tv_advertising_detected(self):
        from backend.llm.semantics import detect_out_of_scope
        for phrase in [
            "Какая эффективность рекламы на ТВ?",
            "Сколько потратили на ТВ?",
            "Сколько потратили на ТВ-рекламу?",
            "Реклама на телевидении по регионам",
            "Покажи расходы на радио-рекламу",
            "Сколько стоили билборды?",
        ]:
            info = detect_out_of_scope(phrase)
            self.assertIsNotNone(info, f"Failed on {phrase}")
            self.assertEqual(info.topic, "tv_ads")
            self.assertIn("ТВ", info.explanation)
            self.assertGreaterEqual(len(info.suggestions), 2)

    def test_salaries_and_inventory_detected(self):
        from backend.llm.semantics import detect_out_of_scope
        for sal_phrase in [
            "Какая зарплата у сотрудников?",
            "Какой фонд оплаты труда?",
            "Какие премии выплачены?",
        ]:
            info_sal = detect_out_of_scope(sal_phrase)
            self.assertIsNotNone(info_sal, f"Failed on salary: {sal_phrase}")
            self.assertEqual(info_sal.topic, "salary")
            self.assertIn("заработной плате", info_sal.explanation)

        for inv_phrase in [
            "Какие остатки на складе?",
            "Сколько товаров на складе?",
            "Какие складские остатки?",
        ]:
            info_inv = detect_out_of_scope(inv_phrase)
            self.assertIsNotNone(info_inv, f"Failed on inventory: {inv_phrase}")
            self.assertEqual(info_inv.topic, "inventory")
            self.assertIn("складских остатков", info_inv.explanation)

    def test_valid_queries_not_flagged_as_out_of_scope(self):
        from backend.llm.semantics import detect_out_of_scope
        valid = [
            "Сколько потратили на рекламу по площадкам?",
            "Какая выручка с учётом скидки по каналам продаж?",
            "Сколько уникальных покупателей в продажах?",
            "Сколько обращений в техподдержку по каждому каналу?",
            "Какие средние фактические трудозатраты в часах по типам задач?",
            "Сколько премиум клиентов совершили покупки?",
            "Какая выручка в премиальном сегменте?",
        ]
        for q in valid:
            self.assertIsNone(detect_out_of_scope(q), f"False positive out-of-scope on: {q}")


class DestructiveMutationTests(unittest.TestCase):
    """Тесты блокировки попыток изменения или удаления данных (DDL/DML) (BI-05, Issue #33)."""

    def test_ddl_and_dml_blocked(self):
        from backend.llm.semantics import check_destructive_mutation
        dangerous = [
            "DROP TABLE data_01_Продажи",
            "DELETE FROM data_01_Продажи WHERE 1=1",
            "UPDATE data_01_Продажи SET status = 'deleted'",
            "ALTER TABLE data_01_Продажи DROP COLUMN price",
            "INSERT INTO data_01_Продажи VALUES ('1', '2')",
            "TRUNCATE TABLE data_01_Продажи",
            "TRUNCATE data_01_Продажи",
            "CREATE TABLE test (id int)",
            "GRANT ALL PRIVILEGES ON DATABASE bi TO hacker",
            "Удали все записи о продажах",
            "Очисти таблицу доставок",
            "Сотри все данные из таблицы продаж",
            "Дропни таблицу",
            "Снеси все записи",
            "Обнови статус обращений в таблице",
            "Вставь новую строку в данные",
            "SELECT * FROM sales; DROP TABLE sales",
        ]
        for cmd in dangerous:
            refusal = check_destructive_mutation(cmd)
            self.assertIsNotNone(refusal, f"Failed to block mutation: {cmd}")
            self.assertEqual(refusal.topic, "mutation_rejected")
            self.assertIn("только на чтение", refusal.explanation)
            self.assertIn("запрещены", refusal.explanation)
            self.assertGreaterEqual(len(refusal.suggestions), 2)
            d = refusal.to_dict()
            self.assertIsNone(d["sql"])
            self.assertIsNone(d["table"])
            self.assertIsNone(d["chart"])
            self.assertIn("только на чтение", d["answer"])

    def test_legitimate_analytical_queries_not_blocked(self):
        from backend.llm.semantics import check_destructive_mutation
        safe = [
            "Как изменилась выручка по сравнению с 2024?",
            "Как изменилось количество проданных штук в 2025 году по сравнению с 2024?",
            "Какая выручка по каналам продаж?",
            "Сколько было операций возврата?",
            "Какой drop rate у пользователей в воронке?",
            "Когда был последний update базы?",
        ]
        for q in safe:
            self.assertIsNone(check_destructive_mutation(q), f"False positive mutation block on: {q}")


class AmbiguousQueryTests(unittest.TestCase):
    """Тесты обработки неоднозначных и слишком общих запросов (BI-05, Issue #33)."""

    def test_ambiguous_queries_detected(self):
        from backend.llm.semantics import detect_ambiguous_query
        vague = [
            "Покажи метрики",
            "Какая статистика?",
            "Сделай сводку",
            "Покажи отчет",
            "Покажи аналитику",
            "Что с эффективностью?",
            "Дай данные",
            "Покажи все метрики",
            "Покажи основные показатели",
            "Общая статистика",
            "Сводка по данным",
            "Что с эффективностью бизнеса?",
            "Какая общая аналитика?",
            "Метрики",
            "Отчет",
            "Показатели",
        ]
        for q in vague:
            refusal = detect_ambiguous_query(q)
            self.assertIsNotNone(refusal, f"Failed to detect ambiguity in: {q}")
            self.assertEqual(refusal.topic, "ambiguous_query")
            self.assertIn("слишком обобщённо", refusal.explanation)
            self.assertIsNotNone(refusal.clarification)
            self.assertGreaterEqual(len(refusal.clarification["options"]), 2)
            d = refusal.to_dict()
            self.assertIsNone(d["sql"])
            self.assertIsNone(d["table"])
            self.assertIsNone(d["chart"])
            self.assertIsNotNone(d["clarification"])

    def test_specific_queries_not_flagged_as_ambiguous(self):
        from backend.llm.semantics import detect_ambiguous_query
        specific = [
            "Какая выручка по каналам продаж?",
            "Сколько обращений в техподдержку?",
            "Топ-5 городов по количеству проданных штук",
            "Сколько кликов по рекламе?",
        ]
        for q in specific:
            self.assertIsNone(detect_ambiguous_query(q), f"False positive ambiguity on: {q}")


if __name__ == "__main__":
    unittest.main()

