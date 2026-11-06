"""CSV and external datasource smoke on a dedicated ephemeral CI PostgreSQL.

NEVER runs against a local developer database. The DB-name guard is mandatory.
"""
import os
import tempfile
import unittest
from pathlib import Path

import psycopg

from db.csv_service import import_uploaded_csv, preview_csv
from backend.app.services.datasources import READ_ONLY_OPTIONS, test_postgres_connection
from backend.llm.sql_runner import PostgresRunner

READER_USER = "bi_ci_reader"
READER_PASSWORD = "ci_ephemeral_reader_password"


@unittest.skipUnless(os.getenv("CI_DATE_AUDIT_DSN"), "only dedicated PostgreSQL CI service")
class ReleasePostgresSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.conn = psycopg.connect(os.environ["CI_DATE_AUDIT_DSN"], autocommit=True)
        if cls.conn.info.dbname != "bi_ci_date_audit":
            cls.conn.close()
            raise RuntimeError("Refusing all fixture writes outside dedicated bi_ci_date_audit")
        cls.conn.execute("CREATE SCHEMA IF NOT EXISTS raw")
        cls.conn.execute("CREATE ROLE bi_ci_reader LOGIN PASSWORD 'ci_ephemeral_reader_password'")
        cls.conn.execute("CREATE TABLE raw.demo_orders (id text, amount text)")
        cls.conn.execute("INSERT INTO raw.demo_orders VALUES ('001', '1,25'), ('002', '2,50'), ('003', '3,75')")
        cls.conn.execute("CREATE TABLE raw.demo_support (ticket text, status text)")
        cls.conn.execute("INSERT INTO raw.demo_support VALUES ('T1', 'Open'), ('T2', 'Closed')")
        cls.conn.execute("GRANT USAGE ON SCHEMA raw TO bi_ci_reader")
        cls.conn.execute("GRANT SELECT ON ALL TABLES IN SCHEMA raw TO bi_ci_reader")

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()

    def test_csv_preview_and_import_preserve_strings_and_reject_overwrite(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "bi_ci_csv_smoke.csv"
            path.write_text(
                "Код;Сумма;Сумма;Канал\n"
                "00123;1,25;2,50;Чат\n"
                "00001;0,00;;Телефон\n"
                "00002;;4,00;Чат\n",
                encoding="utf-8-sig",
            )
            preview = preview_csv(path)
            self.assertEqual(preview["delimiter"], ";")
            self.assertEqual(preview["columns"], ["Код", "Сумма", "Сумма__2", "Канал"])
            self.assertEqual(preview["rows"][0], ["00123", "1,25", "2,50", "Чат"])

            result = import_uploaded_csv(
                self.conn, path, original_filename="bi_ci_csv_smoke.csv", replace=False,
            )
            self.assertEqual(result["row_count"], 3)
            self.assertEqual(result["table_name"], "data_bi_ci_csv_smoke")
            raw = self.conn.execute(
                'SELECT "Код", "Сумма", "Сумма__2" '
                'FROM raw.data_bi_ci_csv_smoke ORDER BY "Код"'
            ).fetchall()
            self.assertEqual(raw[0], ("00001", "0,00", ""))
            self.assertEqual(raw[-1], ("00123", "1,25", "2,50"))

            with self.assertRaisesRegex(RuntimeError, "уже существует"):
                import_uploaded_csv(
                    self.conn, path, original_filename="bi_ci_csv_smoke.csv", replace=False,
                )
            self.assertEqual(
                self.conn.execute("SELECT COUNT(*) FROM raw.data_bi_ci_csv_smoke").fetchone()[0], 3
            )

            cp = Path(d) / "cp1251.csv"
            cp.write_bytes("Код;Статус\n001;Готово\n".encode("cp1251"))
            cp_preview = preview_csv(cp, encoding="cp1251")
            self.assertEqual(cp_preview["rows"][0], ["001", "Готово"])

    def test_external_postgres_discovery_and_readonly_enforcement(self):
        ok, _ms, tables, msg = test_postgres_connection(
            host="127.0.0.1", port=5432, dbname="bi_ci_date_audit",
            user=READER_USER, password=READER_PASSWORD, schema_name="raw",
        )
        self.assertTrue(ok, msg)
        self.assertIn("demo_orders", tables)
        self.assertIn("demo_support", tables)

        with psycopg.connect(
            host="127.0.0.1", port=5432, dbname="bi_ci_date_audit",
            user=READER_USER, password=READER_PASSWORD,
            autocommit=True, options=READ_ONLY_OPTIONS,
        ) as conn:
            self.assertEqual(
                conn.execute("SHOW default_transaction_read_only").fetchone()[0], "on"
            )
            runner = PostgresRunner(conn, schema="raw")
            orders = {"name": "demo_orders", "columns": [
                {"name": "id", "inferred_type": "identifier"},
                {"name": "amount", "inferred_type": "decimal"},
            ]}
            support = {"name": "demo_support", "columns": [
                {"name": "ticket", "inferred_type": "identifier"},
                {"name": "status", "inferred_type": "category"},
            ]}
            self.assertEqual(runner.run(orders, "SELECT COUNT(*) FROM t").rows[0][0], 3)
            self.assertEqual(runner.run(support, "SELECT COUNT(*) FROM t").rows[0][0], 2)
            self.assertEqual(runner.run(orders, "SELECT COUNT(*) FROM t").rows[0][0], 3)

            with self.assertRaises(psycopg.errors.ReadOnlySqlTransaction):
                conn.execute("UPDATE raw.demo_orders SET id = '999'")
            self.assertEqual(runner.run(orders, "SELECT COUNT(*) FROM t").rows[0][0], 3)


if __name__ == "__main__":
    unittest.main()
