"""BI-37 checks independent of PostgreSQL."""
import json
import tempfile
import unittest
from pathlib import Path
from business_glossary import build_glossary, update_glossary


def catalog(kind="category"):
    return {"schema": "raw", "generated_at_utc": "t1", "tables": [
        {"name": "data_01_Продажи", "columns": [
            {"name": "Канал продажи", "postgres_type": "text",
             "inferred_type": kind, "conversion": {"policy": "not_applicable"}},
            {"name": "Количество, шт.", "postgres_type": "text",
             "inferred_type": "integer", "conversion": {"policy": "safe_complete"}},
        ]}
    ]}


class GlossaryTests(unittest.TestCase):
    def test_proposals_not_approved(self):
        result = build_glossary(catalog())
        self.assertEqual(result["summary"]["proposed"], 2)
        self.assertEqual(result["summary"]["approved"], 0)
        self.assertEqual(result["entries"][1]["unit"], "pcs")

    def test_manual_approval_survives_regeneration(self):
        with tempfile.TemporaryDirectory() as root:
            base = Path(root)
            c = base / "catalog.json"
            o = base / "overrides.json"
            g = base / "glossary.json"
            c.write_text(json.dumps(catalog()), encoding="utf-8")
            result = update_glossary(c, o, g, approve={
                "table": "data_01_Продажи", "column": "Канал продажи",
                "description": "Канал оформления заказа, подтверждено аналитиком",
                "unit": None, "synonyms": ["канал", "способ покупки"]})
            self.assertEqual(result["summary"]["approved"], 1)
            again = update_glossary(c, o, g)
            self.assertEqual(again["summary"]["approved"], 1)
            self.assertIn("подтверждено", again["entries"][0]["description"])

    def test_changed_type_requires_review(self):
        old = catalog()
        previous = {"version": 1, "entries": [{
            "schema": "raw", "table": "data_01_Продажи", "column": "Канал продажи",
            "label": "Канал", "description": "Подтверждённый смысл",
            "type_signature": {"postgres_type": "text", "inferred_type": "category"},
            "synonyms": [], "unit": None
        }]}
        result = build_glossary(catalog("integer"), previous)
        self.assertEqual(result["entries"][0]["status"], "needs_review")
        self.assertEqual(result["entries"][0]["source"], "human")
        self.assertEqual(result["summary"]["approved"], 0)

    def test_unknown_field_approval_rejected_without_writing(self):
        with tempfile.TemporaryDirectory() as root:
            base = Path(root)
            c, o, g = base / "c.json", base / "o.json", base / "g.json"
            c.write_text(json.dumps(catalog()), encoding="utf-8")
            with self.assertRaises(ValueError):
                update_glossary(c, o, g, approve={
                    "table": "fake", "column": "field", "description": "Test"})
            self.assertFalse(o.exists())
            self.assertFalse(g.exists())

    def test_invalid_override_rejected(self):
        overrides = {"version": 1, "entries": [{
            "schema": "raw", "table": "data_01_Продажи", "column": "Канал продажи",
            "description": "", "label": "Канал", "type_signature": {}
        }]}
        with self.assertRaises(ValueError):
            build_glossary(catalog(), overrides)

    def test_no_raw_values_or_formulas(self):
        entries = build_glossary(catalog())["entries"]
        self.assertTrue(all("examples" not in e and "formula" not in e for e in entries))


if __name__ == "__main__":
    unittest.main()
