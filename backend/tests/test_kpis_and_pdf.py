"""Unit and integration tests for Business KPIs (BI-32 #78), PDF Export (BI-40 #82),
and SQL security & CTE execution (BI-35 #80, #85).
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.services import dashboards, kpis, saved_queries


class KpisAndPdfTests(unittest.TestCase):
    """Test suite for BI-32, BI-40, and saved query security/CTE fixes."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.client = TestClient(app)

    # -----------------------------------------------------------------
    # BI-32 (Issue #78): Business KPI API & Approval
    # -----------------------------------------------------------------
    def test_business_kpis_list_and_approval(self) -> None:
        """Issue #78 (BI-32): GET /api/kpis returns proposed/approved KPIs and POST /api/kpis/{id}/approve works."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            temp_kpi_file = Path(tmp_dir) / "kpi-definitions.json"
            with patch.object(kpis, "get_kpi_definitions_path", return_value=temp_kpi_file):
                # 1. GET /api/kpis without live calculation
                res_nocalc = self.client.get("/api/kpis?calculate=false")
                self.assertEqual(res_nocalc.status_code, 200)
                data_nocalc = res_nocalc.json()
                self.assertEqual(data_nocalc["schema_name"], "raw")
                self.assertEqual(len(data_nocalc["kpis"]), 8)
                self.assertEqual(data_nocalc["summary"]["proposed"], 8)
                self.assertEqual(data_nocalc["summary"]["approved"], 0)

                # Verify KPI item structure
                first_kpi = data_nocalc["kpis"][0]
                for field in ("id", "table", "title", "unit", "description", "status", "requires_business_approval"):
                    self.assertIn(field, first_kpi)

                # 2. Approve a KPI definition
                approve_payload = {
                    "approved_by": "Chief Financial Officer",
                    "comment": "Формула выручки согласована",
                }
                app_res = self.client.post("/api/kpis/sales_revenue/approve", json=approve_payload)
                self.assertEqual(app_res.status_code, 200)
                app_data = app_res.json()
                self.assertEqual(app_data["id"], "sales_revenue")
                self.assertEqual(app_data["status"], "approved")
                self.assertFalse(app_data["requires_business_approval"])

                # 3. GET /api/kpis reflects the approved status
                res_after = self.client.get("/api/kpis?calculate=false")
                self.assertEqual(res_after.status_code, 200)
                data_after = res_after.json()
                self.assertEqual(data_after["summary"]["approved"], 1)
                self.assertEqual(data_after["summary"]["proposed"], 7)

                # 4. Approving non-existent KPI returns 404
                bad_res = self.client.post("/api/kpis/nonexistent_kpi/approve", json=approve_payload)
                self.assertEqual(bad_res.status_code, 404)

    def test_business_kpis_live_calculation(self) -> None:
        """Issue #78 (BI-32): GET /api/kpis?calculate=true returns calculated values."""
        res = self.client.get("/api/kpis?calculate=true")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(len(data["kpis"]), 8)

        # Control values check on real database
        kpis_by_id = {k["id"]: k for k in data["kpis"]}
        self.assertEqual(kpis_by_id["sales_rows"]["value"], 500000)
        self.assertEqual(kpis_by_id["support_tickets"]["value"], 500000)
        self.assertAlmostEqual(float(kpis_by_id["sales_revenue"]["value"]), 2684326520.31, places=1)
        self.assertIsNotNone(kpis_by_id["sales_revenue"]["formatted_value"])
        self.assertIn("руб.", kpis_by_id["sales_revenue"]["formatted_value"])

    # -----------------------------------------------------------------
    # BI-40 (Issue #82): PDF Export Endpoints
    # -----------------------------------------------------------------
    def test_custom_report_pdf_export(self) -> None:
        """Issue #82 (BI-40): POST /api/export/pdf generates valid PDF with custom payload."""
        payload = {
            "title": "Отчёт по аналитическим данным",
            "subtitle": "Автоматическая выгрузка",
            "datasource_id": "raw",
            "kpis": [
                {
                    "title": "Выручка",
                    "formatted_value": "2 684 326 520,31 руб.",
                    "status": "approved",
                    "description": "Сумма продаж с учётом скидок",
                }
            ],
            "table": {
                "columns": ["Канал", "Выручка"],
                "rows": [["Online", 1500000], ["Retail", 1184326520]],
            },
            "chart": {
                "title": "Выручка по каналам",
                "type": "bar",
                "categories": ["Online", "Retail"],
                "series": [{"name": "Выручка", "data": [1500000, 1184326520]}],
            },
            "sql": "SELECT channel, SUM(amount) FROM raw.sales GROUP BY 1",
        }
        res = self.client.post("/api/export/pdf", json=payload)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.headers.get("content-type"), "application/pdf")
        self.assertTrue(res.content.startswith(b"%PDF"))
        self.assertGreater(len(res.content), 1000)

    def test_dashboard_pdf_export(self) -> None:
        """Issue #82 (BI-40): GET /api/dashboards/{id}/pdf exports saved dashboard as PDF."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            temp_dash_file = Path(tmp_dir) / "dashboards.json"
            with patch.object(dashboards, "get_dashboards_path", return_value=temp_dash_file):
                # 404 for non-existent dashboard
                res_missing = self.client.get("/api/dashboards/nonexistent/pdf")
                self.assertEqual(res_missing.status_code, 404)

                # Create dashboard
                create_res = self.client.post("/api/dashboards", json={
                    "title": "Дашборд директора",
                    "description": "Ключевые метрики за текущий период",
                    "widgets": [
                        {"type": "metric", "title": "Выручка", "content": {"value": "2.68 млрд руб."}},
                        {"type": "text", "title": "Выводы", "content": {"text": "Целевые показатели достигнуты."}},
                    ],
                })
                self.assertEqual(create_res.status_code, 201)
                dash_id = create_res.json()["id"]

                # Export to PDF
                res_dash_pdf = self.client.get(f"/api/dashboards/{dash_id}/pdf")
                self.assertEqual(res_dash_pdf.status_code, 200)
                self.assertEqual(res_dash_pdf.headers.get("content-type"), "application/pdf")
                self.assertTrue(res_dash_pdf.content.startswith(b"%PDF"))
                self.assertGreater(len(res_dash_pdf.content), 1000)

    # -----------------------------------------------------------------
    # BI-35 (Issue #80 & Issue #85): SQL Security & CTE-t execution
    # -----------------------------------------------------------------
    def test_saved_query_security_rejects_ddl(self) -> None:
        """Issue #80 (BI-35): Validate_sql rejects dangerous DDL/DML when executing saved query."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            temp_file = Path(tmp_dir) / "saved_queries.json"
            with patch.object(saved_queries, "get_saved_queries_path", return_value=temp_file):
                bad_create = self.client.post("/api/queries", json={
                    "title": "Опасный SQL",
                    "sql": "DROP TABLE raw.data_01_Продажи; SELECT 1",
                })
                self.assertEqual(bad_create.status_code, 201)
                bad_id = bad_create.json()["id"]

                bad_exec = self.client.post(f"/api/queries/{bad_id}/execute")
                self.assertEqual(bad_exec.status_code, 404)
                self.assertIn("отклонён", bad_exec.json().get("detail", ""))

    def test_saved_query_cte_t_execution_with_comment(self) -> None:
        """Issue #85 (BI-35): CTE alias 't' with header comment executes via PostgresRunner."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            temp_file = Path(tmp_dir) / "saved_queries.json"
            with patch.object(saved_queries, "get_saved_queries_path", return_value=temp_file):
                cte_sql = "-- t = данные таблицы «data_01_Продажи»\nSELECT channel, COUNT(*) FROM t GROUP BY 1"
                create_res = self.client.post("/api/queries", json={
                    "title": "Запрос агента с комментарием t",
                    "sql": cte_sql,
                })
                self.assertEqual(create_res.status_code, 201)
                query_id = create_res.json()["id"]

                with patch("backend.app.services.saved_queries.PostgresRunner") as mock_runner_cls, \
                     patch("backend.app.db.session.get_reader_connection") as mock_conn, \
                     patch("backend.app.services.saved_queries._load_catalog_for_datasource") as mock_cat:
                    mock_conn.return_value = MagicMock()
                    mock_cat.return_value = {
                        "tables": [{
                            "name": "data_01_Продажи",
                            "display_name": "Продажи",
                            "columns": [{"name": "channel"}],
                        }]
                    }
                    mock_run_result = SimpleNamespace(
                        columns=["channel", "count"],
                        rows=[("Online", 400), ("Retail", 600)],
                    )
                    mock_runner_cls.return_value.run.return_value = mock_run_result

                    exec_res = self.client.post(f"/api/queries/{query_id}/execute")
                    self.assertEqual(exec_res.status_code, 200)
                    res_data = exec_res.json()
                    self.assertEqual(res_data["status"], "completed")
                    self.assertIn("выполнен", res_data["result"]["answer"])
                    self.assertEqual(len(res_data["result"]["table"]["rows"]), 2)


if __name__ == "__main__":
    unittest.main()
