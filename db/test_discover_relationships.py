"""Тесты безопасного поиска связей, без живой PostgreSQL."""
import unittest
from unittest.mock import Mock, patch

from discover_relationships import (
    identifier_columns, infer_prefix, choose_pairs, check_reference,
    discover_relationships,
)


class CandidateTests(unittest.TestCase):
    def test_identifier_list(self):
        catalog = {"schema": "raw", "tables": [
            {"name": "sales", "columns": [
                {"name": "Код позиции", "inferred_type": "identifier"},
                {"name": "Сумма", "inferred_type": "decimal"},
                {"name": "Код исходной позиции", "inferred_type": "identifier"},
            ]}]}
        schema, ids = identifier_columns(catalog)
        self.assertEqual(schema, "raw")
        self.assertEqual(len(ids), 2)

    def test_prefix_not_just_numeric_suffix(self):
        self.assertEqual(infer_prefix(["ПР-КЛ-123", "ПР-КЛ-456"] * 6)["dominant_prefix"],
                         "ПР-КЛ-")
        self.assertIsNone(infer_prefix(["ПР-КЛ-123", "ПД-КЛ-123"] * 6)["dominant_prefix"])
        self.assertIsNone(infer_prefix(["123456"] * 20)["dominant_prefix"])
        self.assertIsNone(infer_prefix(["ПР-КЛ-123"] * 2)["dominant_prefix"])

    def test_pair_direction_and_prefix_safety(self):
        columns = [
            {"table": "sales", "column": "Код позиции", "dominant_prefix": "ПР-ПЗ-"},
            {"table": "sales", "column": "Код исходной позиции",
             "dominant_prefix": "ПР-ПЗ-"},
            {"table": "support", "column": "Код клиента", "dominant_prefix": "ПД-КЛ-"},
            {"table": "sales", "column": "Код покупателя", "dominant_prefix": "ПР-КЛ-"},
        ]
        pairs, skipped = choose_pairs(columns, 20)
        self.assertEqual(len(pairs), 1)
        self.assertEqual(pairs[0]["source"]["column"], "Код исходной позиции")
        self.assertEqual(pairs[0]["target"]["column"], "Код позиции")
        self.assertEqual(pairs[0]["kind"], "self_reference_candidate")
        self.assertGreater(skipped, 0)

    def test_pair_limit(self):
        columns = [
            {"table": f"t{i}", "column": "ref", "dominant_prefix": "K-"}
            for i in range(10)
        ]
        with self.assertRaises(ValueError):
            choose_pairs(columns, 2)

    def test_reject_system_schema(self):
        with self.assertRaises(ValueError):
            identifier_columns({"schema": "pg_catalog", "tables": []})


class FullVerificationTests(unittest.TestCase):
    def test_verified_never_approves_join(self):
        conn = Mock()
        conn.execute.side_effect = [
            Mock(fetchone=Mock(return_value=(5819, 5819))),
            Mock(fetchone=Mock(return_value=(500000, 0, 500000))),
        ]
        evidence = check_reference(
            conn, "raw",
            {"table": "sales", "column": "Код исходной позиции"},
            {"table": "sales", "column": "Код позиции"},
        )
        self.assertTrue(evidence["referential_integrity_verified"])
        self.assertFalse(evidence["can_auto_join"])
        self.assertFalse(evidence["business_semantics_approved"])
        self.assertEqual(evidence["target_key_stats"]["duplicate_rows"], 0)
        rendered = conn.execute.call_args_list[0].args[0].as_string()
        self.assertIn('"raw"."sales"', rendered)
        self.assertIn('"Код исходной позиции"', rendered)

    def test_duplicate_target_is_unsafe(self):
        conn = Mock()
        conn.execute.side_effect = [
            Mock(fetchone=Mock(return_value=(10, 10))),
            Mock(fetchone=Mock(return_value=(100, 0, 98))),
        ]
        result = check_reference(
            conn, "raw", {"table": "x", "column": "source"},
            {"table": "x", "column": "target"},
        )
        self.assertFalse(result["referential_integrity_verified"])

    @patch("discover_relationships.check_reference")
    @patch("discover_relationships.sample_identifier")
    def test_pipeline_keeps_hypotheses_separate(self, sampling, auditing):
        sampling.side_effect = [
            {"sample_nonempty": 30, "dominant_prefix": "S-",
             "dominant_ratio": 1.0},
            {"sample_nonempty": 30, "dominant_prefix": "S-",
             "dominant_ratio": 1.0},
        ]
        auditing.return_value = {
            "source_nonempty_rows": 7, "matched_source_rows": 7,
            "unmatched_source_rows": 0, "referential_integrity_verified": True,
            "can_auto_join": False, "business_semantics_approved": False,
        }
        catalog = {"schema": "raw", "generated_at_utc": "p1",
                   "tables": [{"name": "s", "columns": [
                       {"name": "Код позиции", "inferred_type": "identifier"},
                       {"name": "Код исходной позиции", "inferred_type": "identifier"},
                   ]}]}
        out = discover_relationships(Mock(), catalog)
        self.assertEqual(out["summary"]["candidate_pairs"], 1)
        self.assertEqual(out["summary"]["technically_verified"], 1)
        self.assertEqual(out["summary"]["approved_joins"], 0)
        self.assertFalse(out["relationships"][0]["can_auto_join"])


if __name__ == "__main__":
    unittest.main()
