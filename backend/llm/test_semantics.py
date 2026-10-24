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


if __name__ == "__main__":
    unittest.main()
