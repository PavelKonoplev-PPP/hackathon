"""Regression coverage for fixes that exist in older LLM PRs (#130/#132).

These cases protect final integration from silently losing semantics fixes.
No database, model or external services required.
"""
import unittest

from backend.llm.semantics import Intent, check_sql, same_word, strong_word


def checked(query, sql, *, values=None, numeric=None, columns=None, strict=True):
    return check_sql(
        sql, query, Intent.parse(query), values or {},
        numeric_columns=numeric, columns=columns, strict=strict,
    )


class FinalIntegrationGuardTests(unittest.TestCase):
    def test_years_are_never_stemmed_together(self):
        self.assertFalse(same_word("2025", "2026"))
        self.assertFalse(strong_word("2025", "2026"))
        self.assertTrue(same_word("2025", "2025"))

    def test_average_rating_must_use_numeric_rating_column(self):
        q = "Какая средняя оценка клиента по каналам обращения?"
        bad = ('SELECT "Канал обращения", 100.0 * COUNT(*) / COUNT(*) '
               'FROM t GROUP BY "Канал обращения"')
        problems = checked(q, bad, numeric=["Оценка клиента"],
                           columns=["Канал обращения", "Оценка клиента"])
        self.assertTrue(any('AVG("Оценка клиента")' in item for item in problems), problems)
        good = ('SELECT "Канал обращения", AVG("Оценка клиента") '
                'FROM t GROUP BY "Канал обращения"')
        self.assertEqual(checked(q, good, numeric=["Оценка клиента"],
                                 columns=["Канал обращения", "Оценка клиента"]), [])

    def test_reject_unrequested_null_with_named_category(self):
        q = "Сколько строк продаж без карты?"
        values = {"Уровень программы лояльности": ["Без карты лояльности"]}
        sql = ('SELECT COUNT(*) FROM t WHERE "Код покупателя" IS NULL AND '
               '"Уровень программы лояльности" = \'Без карты лояльности\'')
        errors = checked(q, sql, values=values,
                         columns=["Код покупателя", "Уровень программы лояльности"])
        self.assertTrue(any("Код покупателя" in item for item in errors), errors)

    def test_allow_is_not_null_on_named_numeric_metric(self):
        q = "Какая средняя оценка клиента по каналам обращения?"
        sql = ('SELECT "Канал обращения", AVG("Оценка клиента") FROM t '
               'WHERE "Оценка клиента" IS NOT NULL GROUP BY "Канал обращения"')
        self.assertEqual(checked(q, sql, numeric=["Оценка клиента"],
                                 columns=["Канал обращения", "Оценка клиента"]), [])

    def test_allow_unrequested_null_without_named_category(self):
        q = "Сколько строк с пустым кодом клиента?"
        sql = 'SELECT COUNT(*) FROM t WHERE "Код клиента" IS NULL'
        self.assertEqual(checked(q, sql, columns=["Код клиента"]), [])

    def test_valid_subquery_not_banned_indiscriminately(self):
        q = "Сколько заказов с суммой больше среднего?"
        sql = ('SELECT COUNT(*) FROM t WHERE "Сумма" > '
               '(SELECT AVG("Сумма") FROM t)')
        self.assertEqual(checked(q, sql, numeric=["Сумма"], columns=["Сумма"]), [])

    def test_reject_inverted_named_category_subquery(self):
        q = "Сколько уникальных заказов без карты?"
        values = {"Уровень программы лояльности": ["Без карты лояльности"]}
        sql = ('SELECT COUNT(DISTINCT "Код заказа") FROM t WHERE "Код покупателя" NOT IN '
               '(SELECT "Код покупателя" FROM t WHERE '
               '"Уровень программы лояльности" = \'Без карты лояльности\')')
        problems = checked(q, sql, values=values,
                           columns=["Код заказа", "Код покупателя", "Уровень программы лояльности"])
        self.assertTrue(problems)


if __name__ == "__main__":
    unittest.main()
