"""Unit tests for BI-06 without PostgreSQL or LLM."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from run_golden_checks import load_checks, run_checks, connect_readonly


class GoldenTests(unittest.TestCase):
    def test_manifest_has_25_goldens(self):
        checks = load_checks()
        self.assertEqual(len(checks), 25)
        self.assertEqual(len({x["id"] for x in checks}), 25)
        self.assertTrue(any(x["expected"] == 500000 for x in checks))
        self.assertTrue(any(x["expected"] == 5819 for x in checks))

    def test_readonly_runner_pass(self):
        cur = Mock()
        cur.fetchone.side_effect = [(5819,), None]
        conn = Mock()
        conn.transaction.return_value.__enter__ = Mock()
        conn.transaction.return_value.__exit__ = Mock(return_value=False)
        conn.cursor.return_value.__enter__ = Mock(return_value=cur)
        conn.cursor.return_value.__exit__ = Mock(return_value=False)
        report = run_checks(conn, [{"id": "returns", "sql": "SELECT 5819", "expected": 5819}])
        self.assertEqual(report["passed"], 1)
        self.assertEqual(report["failed"], 0)
        self.assertEqual(report["errors"], 0)
        conn.execute.assert_any_call("SET TRANSACTION READ ONLY")

    def test_runner_fails_on_bad_count(self):
        cur = Mock()
        cur.fetchone.side_effect = [(10,), None]
        conn = Mock()
        conn.transaction.return_value.__enter__ = Mock()
        conn.transaction.return_value.__exit__ = Mock(return_value=False)
        conn.cursor.return_value.__enter__ = Mock(return_value=cur)
        report = run_checks(conn, [{"id": "returns", "sql": "SELECT 10", "expected": 5819}])
        self.assertEqual(report["failed"], 1)
        self.assertEqual(report["results"][0]["actual"], 10)

    def test_empty_manifest_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "checks.json"
            path.write_text(json.dumps({"version": 1, "checks": []}))
            with self.assertRaises(ValueError):
                load_checks(path)

    def test_reader_password_required(self):
        with patch.dict("os.environ", {"POSTGRES_READER_USER": "bi_reader"}, clear=True):
            with self.assertRaisesRegex(ValueError, "POSTGRES_READER_PASSWORD"):
                connect_readonly()

    def test_owner_is_rejected(self):
        with patch.dict("os.environ", {"POSTGRES_READER_USER": "bi_user",
                                        "POSTGRES_READER_PASSWORD": "secret"}, clear=True):
            with self.assertRaisesRegex(ValueError, "bi_reader"):
                connect_readonly()


if __name__ == "__main__":
    unittest.main()
