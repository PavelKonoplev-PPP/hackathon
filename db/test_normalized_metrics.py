"""Тесты календарно-нормированных показателей без подключений к базе."""
import unittest
from datetime import date

from normalized_metrics import calendar_days, comparison_sql


def query(**kwargs):
    fields = dict(
        schema="raw", table="orders",
        group_column="Препарат",
        entity_column="Аптека",
        date_column="Дата",
        quantity_column="Количество",
        first_start=date(2025, 2, 1),
        first_end=date(2025, 3, 1),
        second_start=date(2025, 3, 1),
        second_end=date(2025, 4, 1),
        top_n=3,
    )
    fields.update(kwargs)
    return comparison_sql(**fields)


class CalendarNormalizedTests(unittest.TestCase):
    def test_february_and_march(self):
        self.assertEqual(calendar_days(date(2025, 2, 1), date(2025, 3, 1)), 28)
        self.assertEqual(calendar_days(date(2024, 2, 1), date(2024, 3, 1)), 29)
        self.assertEqual(calendar_days(date(2025, 3, 1), date(2025, 4, 1)), 31)

    def test_quarter(self):
        self.assertEqual(calendar_days(date(2025, 1, 1), date(2025, 4, 1)), 90)

    def test_partial_month_rejected(self):
        with self.assertRaisesRegex(ValueError, "полных календарных"):
            calendar_days(date(2025, 2, 5), date(2025, 3, 1))

    def test_empty_or_reverse_period_rejected(self):
        for first, second in [
            (date(2025, 2, 1), date(2025, 2, 1)),
            (date(2025, 3, 1), date(2025, 2, 1)),
        ]:
            with self.subTest(first=first, second=second):
                with self.assertRaises(ValueError):
                    calendar_days(first, second)

    def test_overlap_rejected(self):
        with self.assertRaisesRegex(ValueError, "не пересекаться"):
            query(second_start=date(2025, 2, 1))

    def test_sql_uses_all_objects_not_only_active_in_month(self):
        sql_obj, values = query()
        statement = sql_obj.as_string()
        self.assertIn('COUNT(DISTINCT entity_value) AS objects FROM events', statement)
        self.assertIn('p.objects * b.first_days', statement)
        self.assertIn('p.objects * b.second_days', statement)
        self.assertIn("NULLIF(p.objects * b.first_days, 0)", statement)
        self.assertIn("NULLIF(p.objects * b.second_days, 0)", statement)
        self.assertIn("NULLIF(t.first_total / b.first_days, 0)", statement)
        self.assertEqual(values[4:6], (28, 31))
        self.assertEqual(values[-1], 3)

    def test_zero_events_not_lost_within_period(self):
        statement = query()[0].as_string()
        self.assertIn("COALESCE(SUM(quantity) FILTER", statement)
        self.assertIn("NULLS LAST", statement)
        self.assertIn("WHERE group_value IS NOT NULL", statement)

    def test_safe_types_and_quoted_identifiers(self):
        statement = query(
            table='some"; DROP TABLE raw.sales; --',
            group_column='Препарат" сложный',
            entity_column='Аптека" номер',
        )[0].as_string()
        self.assertIn('"raw"."some""; DROP TABLE raw.sales; --"', statement)
        self.assertIn('"Препарат"" сложный"', statement)
        self.assertIn('"Аптека"" номер"', statement)
        self.assertIn("pg_input_is_valid(", statement)
        self.assertIn("::numeric", statement)
        self.assertNotIn("INSERT INTO", statement)
        self.assertNotIn("DROP TABLE raw.sales", statement.replace('"raw"."some""; DROP TABLE raw.sales; --"', ""))

    def test_timestamp_date_kind(self):
        statement = query(date_kind="timestamp")[0].as_string()
        self.assertIn("timestamp without time zone", statement)
        self.assertIn("::date", statement)

    def test_invalid_date_kind_rejected(self):
        with self.assertRaises(ValueError):
            query(date_kind="identifier")

    def test_invalid_limit_rejected(self):
        for value in (0, -1, 201, "3", True):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    query(top_n=value)

    def test_no_limit(self):
        statement, values = query(top_n=None)
        self.assertNotIn("LIMIT %s", statement.as_string())
        self.assertEqual(len(values), 6)

    def test_missing_field_rejected(self):
        with self.assertRaises(ValueError):
            query(group_column="")

    def test_rate_math(self):
        # Независимая проверка формулы на искусственной совокупности:
        # 2 объекта, февраль 28 дней, март 31 день; события есть не каждый день.
        feb_total, mar_total, objects = 56, 93, 2
        feb_avg = feb_total / (objects * 28)
        mar_avg = mar_total / (objects * 31)
        growth = 100 * ((mar_total / 31) / (feb_total / 28) - 1)
        self.assertEqual(feb_avg, 1)
        self.assertEqual(mar_avg, 1.5)
        self.assertEqual(round(growth, 2), 50)

    def test_empty_population_does_not_divide_by_zero(self):
        statement = query()[0].as_string()
        self.assertIn("WHERE p.objects > 0", statement)
        self.assertIn("NULLIF", statement)


if __name__ == "__main__":
    unittest.main()
