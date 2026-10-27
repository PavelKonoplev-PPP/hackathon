"""Утверждённые бизнес-KPI в агенте (BI-32 / #57) — без модели и БД."""
import json
import os
import tempfile
import unittest
from pathlib import Path

from backend.llm import kpis as kp
from backend.llm.ask import _SESSIONS, answer_question, choose_table
from backend.llm.test_ask import FakeRunner, ScriptedClient

REAL_CATALOG = Path(__file__).resolve().parents[2] / "data" / "schema-catalog.json"
ADS = "data_06_Рекламные_кампании"
CTR = '100.0 * SUM("Клики") / NULLIF(SUM("Показы"), 0)'


def kpi(id_, table, expression, status="approved", title=None):
    return {"id": id_, "schema": "raw", "table": table, "title": title or id_, "unit": "%",
            "description": "Отношение суммы кликов к сумме показов ×100.", "expression": expression,
            "status": status, "approved_by": "Аналитик"}


class KpiFile:
    """Временный data/kpi-definitions.json и KPI_DEFINITIONS_PATH на время теста."""

    def __init__(self, test, items):
        d = tempfile.TemporaryDirectory()
        test.addCleanup(d.cleanup)
        self.path = Path(d.name) / "kpi-definitions.json"
        self.path.write_text(json.dumps({"version": 1, "schema": "raw", "kpis": items},
                                        ensure_ascii=False), encoding="utf-8")
        old = os.environ.get("KPI_DEFINITIONS_PATH")
        os.environ["KPI_DEFINITIONS_PATH"] = str(self.path)
        test.addCleanup(lambda: os.environ.pop("KPI_DEFINITIONS_PATH", None) if old is None
                        else os.environ.__setitem__("KPI_DEFINITIONS_PATH", old))
        kp._CACHE.clear()
        test.addCleanup(kp._CACHE.clear)


class LoadAndMatchTests(unittest.TestCase):
    def test_only_approved_with_expression(self):
        KpiFile(self, [kpi("ads_ctr", ADS, CTR, title="CTR"),
                       kpi("ads_clicks", ADS, 'SUM("Клики")', status="proposed"),
                       kpi("broken", ADS, None)])
        self.assertEqual([k.id for k in kp.load()], ["ads_ctr"])

    def test_no_file(self):
        KpiFile(self, []).path.unlink()
        self.assertEqual(kp.load(), [])

    def test_triggers_prefer_narrow_kpi(self):
        approved = [kp.Kpi("ads_clicks", "raw", ADS, "Клики", 'SUM("Клики")', None, "", None),
                    kp.Kpi("ads_ctr", "raw", ADS, "CTR", CTR, "%", "", None)]
        table = {"name": ADS}
        self.assertEqual(kp.for_question("Какой CTR (отношение кликов к показам)?", table, "raw",
                                         approved).id, "ads_ctr")
        self.assertEqual(kp.for_question("Сколько кликов по площадкам?", table, "raw", approved).id,
                         "ads_clicks")
        self.assertIsNone(kp.for_question("Сколько показов?", table, "raw", approved))
        self.assertIsNone(kp.for_question("Какой CTR?", {"name": "другая"}, "raw", approved))
        self.assertIsNone(kp.for_question("Какой CTR?", table, "public", approved))

    def test_check_ignores_spaces_and_case(self):
        k = kp.Kpi("ads_ctr", "raw", ADS, "CTR", CTR, "%", "", None)
        good = 'SELECT "Площадка", 100.0*sum("Клики")/nullif(sum("Показы"),0) FROM t GROUP BY 1'
        self.assertEqual(kp.check(good, k), [])
        bad = 'SELECT "Площадка", SUM("Клики")::float / SUM("Показы") FROM t GROUP BY 1'
        self.assertIn("утверждённый KPI", kp.check(bad, k)[0])


class AgentTests(unittest.TestCase):
    CAT = {"schema": "raw", "tables": [{"name": ADS, "columns": [
        {"name": "Площадка", "inferred_type": "category", "conversion": {}},
        {"name": "Клики", "inferred_type": "integer",
         "conversion": {"target_type": "integer", "policy": "safe_complete"}},
        {"name": "Показы", "inferred_type": "integer",
         "conversion": {"target_type": "integer", "policy": "safe_complete"}}]}]}

    def setUp(self):
        _SESSIONS.clear()

    def test_formula_enforced_and_named_in_answer(self):
        KpiFile(self, [kpi("ads_ctr", ADS, CTR, title="CTR")])
        wrong = '```sql\nSELECT "Площадка", SUM("Клики")::float / SUM("Показы") AS "CTR" FROM t GROUP BY 1\n```'
        right = f'```sql\nSELECT "Площадка", {CTR} AS "CTR" FROM t GROUP BY 1\n```'
        client = ScriptedClient([wrong, right, "У Яндекс Директ CTR 2,50."])
        runner = FakeRunner(rows=[("Яндекс Директ", 2.5), ("VK Реклама", 1.9)], columns=["Площадка", "CTR"])
        trace = {}
        result = answer_question("Какой CTR по площадкам?", self.CAT, runner, client=client, trace=trace)
        self.assertEqual(trace["kpi"], "ads_ctr")
        self.assertIn(f"утверждённой формуле: {CTR}", client.calls[0][0]["content"])
        self.assertEqual(trace["attempts"], 2)
        self.assertIn("утверждённый KPI", trace["errors"][0])
        self.assertIn("Расчёт по утверждённому определению KPI «CTR», утвердил: Аналитик.",
                      result["answer"])

    def test_without_approval_agent_works_as_before(self):
        KpiFile(self, [kpi("ads_ctr", ADS, CTR, status="proposed")])
        sql = '```sql\nSELECT "Площадка", SUM("Клики")::float / SUM("Показы") AS "CTR" FROM t GROUP BY 1\n```'
        trace = {}
        result = answer_question("Какой CTR по площадкам?", self.CAT,
                                 FakeRunner(rows=[("Яндекс Директ", 0.025)], columns=["Площадка", "CTR"]),
                                 client=ScriptedClient([sql, "CTR 0,03."]), trace=trace)
        self.assertNotIn("kpi", trace)
        self.assertEqual(trace["errors"], [])
        self.assertNotIn("утверждённому", result["answer"])


@unittest.skipUnless(REAL_CATALOG.is_file(), "нет data/schema-catalog.json")
class TableChoiceTests(unittest.TestCase):
    def test_kpi_name_selects_table(self):
        tables = json.loads(REAL_CATALOG.read_text(encoding="utf-8"))["tables"]
        KpiFile(self, [])
        self.assertIsNone(choose_table("Какой CTR?", tables, {})[0])  # без KPI таблица неясна
        KpiFile(self, [kpi("ads_ctr", ADS, CTR, title="CTR")])
        table, _ = choose_table("Какой CTR?", tables, {})
        self.assertEqual(table["name"], ADS)


if __name__ == "__main__":
    unittest.main()
