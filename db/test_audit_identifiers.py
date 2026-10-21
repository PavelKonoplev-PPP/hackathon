"""Unit-тесты аудита неклассифицированных ID (без PostgreSQL)."""
import unittest
from unittest.mock import Mock, patch

from audit_identifiers import (
    unprefixed_identifiers, query_identifier_stats, audit_identifier,
    audit_unprefixed,
)


EXAMPLE = {
    "schema": "raw",
    "generated_at_utc": "ts1",
    "identifiers": [
        {"table": "sales", "column": "Код магазина", "dominant_prefix": None},
        {"table": "sales", "column": "Код покупателя", "dominant_prefix": "ПР-КЛ-"},
        {"table": "pharmacy", "column": "ID_Аптеки", "dominant_prefix": None},
    ],
}


class AuditIdentifierTests(unittest.TestCase):
    def test_select_only_unprefixed(self):
        schema, cols = unprefixed_identifiers(EXAMPLE)
        self.assertEqual(schema, "raw")
        self.assertEqual([c["column"] for c in cols], ["Код магазина", "ID_Аптеки"])

    def test_reject_system_schema(self):
        with self.assertRaises(ValueError):
            unprefixed_identifiers({**EXAMPLE, "schema": "pg_catalog"})

    def test_quoted_identifiers_and_read_only_select(self):
        q = query_identifier_stats("raw", "data_09_Лекарственные_препараты",
                                   "ID_Аптеки").as_string()
        self.assertIn('"raw"."data_09_Лекарственные_препараты"', q)
        self.assertIn('"ID_Аптеки"', q)
        self.assertIn("WITH value_counts AS", q)
        self.assertNotIn("DROP", q)

    def test_real_aggregate_output(self):
        conn = Mock()
        conn.execute.side_effect = [
            Mock(fetchone=Mock(return_value=(1,))),
            Mock(fetchone=Mock(return_value=(500, 0, 39, 35, 496, 32, 39))),
        ]
        out = audit_identifier(conn, "raw", "pharmacy", "ID_Аптеки")
        self.assertEqual(out["distinct_keys"], 39)
        self.assertEqual(out["duplicate_rows"], 461)
        self.assertTrue(out["all_nonempty_keys_numeric"])
        self.assertTrue(out["can_group_by"])
        self.assertFalse(out["can_auto_join"])

    @patch("audit_identifiers.audit_identifier")
    def test_summary_without_implicit_join_permission(self, audit):
        audit.return_value = {"status": "checked", "can_group_by": True,
                              "can_auto_join": False}
        out = audit_unprefixed(Mock(), EXAMPLE)
        self.assertEqual(out["summary"]["checked_fields"], 2)
        self.assertEqual(out["summary"]["automatically_approved_joins"], 0)


if __name__ == "__main__":
    unittest.main()
