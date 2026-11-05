import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

try:
    from backend.app.main import app
    from backend.app.services import dashboard_builder as db
    from backend.app.services import dashboards, query_tracker
except ImportError:
    from app.main import app
    from app.services import dashboard_builder as db
    from app.services import dashboards, query_tracker

SALES = {
    "name": "data_01_Продажи",
    "columns": [
        {"name": "Дата операции (МСК)", "inferred_type": "timestamp"},
        {"name": "Канал продажи", "inferred_type": "category"},
        {"name": "Город", "inferred_type": "category"},
        {"name": "Количество, шт.", "inferred_type": "integer"},
        {"name": "Цена до скидки за единицу, руб.", "inferred_type": "decimal"},
    ],
}
SUPPORT = {
    "name": "data_02_Техническая_поддержка",
    "columns": [
        {"name": "Создано (МСК)", "inferred_type": "timestamp"},
        {"name": "Канал обращения", "inferred_type": "category"},
    ],
}
CATALOG = {"schema": "raw", "tables": [SALES, SUPPORT]}


class FakeClient:
    def __init__(self, text):
        self.text = text

    def chat(self, messages, **kwargs):
        if isinstance(self.text, Exception):
            raise self.text
        return self.text


PLAN = FakeClient('["Выручка по кварталам", "Выручка по каналам", "Выручка по городам"]')


def good(question, **kwargs):
    return {
        "answer": "Ответ",
        "status": "completed",
        "sql": f"SELECT 1 -- {question}",
        "table_name": kwargs.get("table_name"),
        "table": {"columns": ["x", "y"], "rows": [["a", 1]]},
        "chart": {"type": "bar", "title": "t", "categories": ["a"], "series": [{"name": "y", "data": [1]}]},
    }


class PlanTests(unittest.TestCase):
    def test_subject_without_command(self):
        self.assertEqual(db.request_subject("Создай дашборд по продажам"), "по продажам")
        self.assertEqual(db.request_subject("Собери дашборд"), "")

    def test_parse_plan_ignores_garbage(self):
        self.assertEqual(db.parse_plan("не json"), [])
        self.assertEqual(db.parse_plan('{"a": 1}'), [])
        self.assertEqual(db.parse_plan('Вот: ["Выручка по месяцам", "Выручка по месяцам", 5, "Создай дашборд"]'),
                         ["Выручка по месяцам"])

    def test_parse_plan_unwraps_question_objects(self):
        text = '[{"Вопрос": "Выручка по каналам"}, {"Показатель": "мусор"}]'
        self.assertEqual(db.parse_plan(text), ["Выручка по каналам"])

    def test_model_failure_falls_back_to_catalog(self):
        planned, _ = db.plan_questions("по продажам", SALES, FakeClient(RuntimeError("нет модели")))
        self.assertGreaterEqual(len(planned), db.MIN_WIDGETS)
        self.assertIn("Выручка по месяцам", planned)

    def test_grain_from_request(self):
        self.assertIn("Выручка по кварталам", db.catalog_questions(SALES, "ежеквартальная статистика"))

    def test_dimension_named_in_request_comes_first(self):
        questions = db.catalog_questions(SALES, "с выручкой по городам")
        self.assertEqual(questions[1], "Выручка по «Город»")

    def test_table_without_money_is_counted_not_summed(self):
        questions = db.catalog_questions(SUPPORT)
        self.assertGreaterEqual(len(questions), 2)
        self.assertTrue(all(q.startswith("Количество записей") for q in questions))

    def test_metric_named_in_request_wins(self):
        table = {"name": "data_fleet", "columns": [
            {"name": "Рейс", "inferred_type": "text"},
            {"name": "Дата рейса", "inferred_type": "date"},
            {"name": "Депо", "inferred_type": "category"},
            {"name": "Пассажиры", "inferred_type": "integer"},
            {"name": "Выручка рейса, руб.", "inferred_type": "decimal"},
            {"name": "Опоздание, мин", "inferred_type": "integer"},
        ]}
        self.assertEqual(db.catalog_questions(table, "с пассажирами по депо")[0], "Сумма «Пассажиры» по месяцам")
        self.assertEqual(db.catalog_questions(table, "с опозданием")[0], "Среднее «Опоздание, мин» по месяцам")
        self.assertEqual(db.catalog_questions(table, "по рейсам")[0], "Сумма «Выручка рейса, руб.» по месяцам")

    def test_model_only_adds_to_plan(self):
        planned, _ = db.plan_questions("по продажам", SALES, FakeClient('["Средняя цена по городам"]'))
        self.assertEqual(planned[-1], "Средняя цена по городам")
        self.assertIn("Выручка по месяцам", planned)

    def test_title_keeps_request_wording(self):
        self.assertEqual(db.dashboard_title("Создай дашборд по продажам", SALES), "Дашборд по продажам")
        self.assertEqual(db.dashboard_title("Собери дашборд", SALES), "Дашборд: Продажи")


class WidgetProblemTests(unittest.TestCase):
    def test_good_result(self):
        self.assertIsNone(db.widget_problem(good("q")))

    def test_rejects_unreliable_results(self):
        self.assertTrue(db.widget_problem({"status": "refused", "sql": "x"}))
        self.assertTrue(db.widget_problem({**good("q"), "clarification": {"question": "?", "options": []}}))
        self.assertTrue(db.widget_problem({**good("q"), "sql": None}))
        self.assertTrue(db.widget_problem({"status": "completed", "sql": "x", "table": {"columns": [], "rows": []}}))
        zero = good("q")
        zero["chart"]["series"][0]["data"] = [0]
        self.assertTrue(db.widget_problem(zero))

    def test_rejects_count_of_measure(self):
        res = {**good("q"), "sql": 'SELECT "Депо", COUNT("Количество, шт.") FROM t GROUP BY 1'}
        self.assertTrue(db.widget_problem(res, "Количество по депо", SALES))
        ok = {**good("q"), "sql": 'SELECT "Депо", COUNT(*), COUNT("Город") FROM t GROUP BY 1'}
        self.assertIsNone(db.widget_problem(ok, "Количество по депо", SALES))

    def test_rejects_wrong_metric_label(self):
        res = good("q")
        res["chart"]["series"][0]["name"] = "Выручка, ч"
        self.assertTrue(db.widget_problem(res, "Количество записей по месяцам"))
        self.assertIsNone(db.widget_problem(res, "Выручка по месяцам"))


class BuildTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        path = patch.object(dashboards, "get_dashboards_path", return_value=Path(self.tmp.name) / "d.json")
        path.start()
        self.addCleanup(path.stop)

    def build(self, request="Создай дашборд по продажам с выручкой", ask=good, client=PLAN, **kw):
        return db.build_dashboard(request, catalog=CATALOG, client=client, ask=ask, **kw)

    def saved(self):
        return dashboards.load_dashboards_file()

    def test_creates_dashboard_with_sql_and_source(self):
        res = self.build()
        self.assertEqual(res["status"], "completed")
        self.assertGreaterEqual(res["dashboard"]["widget_count"], 3)
        stored = self.saved()[0]
        self.assertEqual(stored["id"], res["dashboard"]["id"])
        for w in stored["widgets"]:
            self.assertTrue(w["sql"])
            self.assertEqual(w["source_table"], "Продажи")

    def test_each_widget_uses_its_own_session_and_chosen_table(self):
        calls = []

        def ask(question, **kw):
            calls.append(kw)
            return good(question, **kw)

        self.build(ask=ask)
        self.assertEqual(len({c["session_id"] for c in calls}), len(calls))
        self.assertTrue(all(c["table_name"] == "data_01_Продажи" for c in calls))

    def test_failed_widget_is_reported_and_left_out(self):
        def ask(question, **kw):
            if question == "Выручка по кварталам":
                return {"answer": "нет", "status": "completed", "sql": None}
            return good(question, **kw)

        res = self.build(ask=ask)
        self.assertEqual(res["status"], "completed")
        self.assertIn("Выручка по кварталам", [f["question"] for f in res["dashboard"]["failed"]])
        self.assertNotIn("Выручка по кварталам", [w["title"] for w in self.saved()[0]["widgets"]])

    def test_reserve_is_used_when_plan_is_not_enough(self):
        def ask(question, **kw):
            if question.startswith("Выручка"):
                return {"answer": "нет", "status": "completed", "sql": None}
            return good(question, **kw)

        res = self.build(ask=ask, client=FakeClient("нет плана"))
        self.assertEqual(res["status"], "completed")
        self.assertEqual(res["dashboard"]["widget_count"], 3)

    def test_not_saved_when_too_few_widgets(self):
        res = self.build(ask=lambda q, **kw: {"answer": "нет", "status": "completed"})
        self.assertEqual(res["status"], "failed")
        self.assertIn("не создан", res["answer"])
        self.assertEqual(self.saved(), [])

    def test_duplicate_sql_does_not_count(self):
        def ask(question, **kw):
            return {**good(question, **kw), "sql": "SELECT 1"}

        res = self.build(ask=ask)
        self.assertEqual(res["status"], "failed")
        self.assertEqual(self.saved(), [])

    def test_agent_error_text_is_hidden(self):
        def ask(question, **kw):
            raise RuntimeError("password=secret host=db")

        res = self.build(ask=ask)
        self.assertEqual(res["status"], "failed")
        self.assertNotIn("secret", res["answer"])

    def test_cancel_saves_nothing(self):
        res = self.build(is_cancelled=lambda: True)
        self.assertEqual(res["status"], "cancelled")
        self.assertEqual(self.saved(), [])

    def test_time_budget_stops_work(self):
        res = self.build(budget_s=-1)
        self.assertEqual(res["status"], "failed")
        self.assertEqual(self.saved(), [])

    def test_mutation_is_refused(self):
        res = self.build(request="Создай дашборд и удали таблицу продаж")
        self.assertEqual(res["status"], "refused")
        self.assertEqual(self.saved(), [])

    def test_unknown_subject_asks_for_table(self):
        res = self.build(request="Создай дашборд про котиков")
        self.assertEqual(res["status"], "clarification")
        self.assertTrue(all("дашборд" in o and "котик" not in o for o in res["clarification"]["options"]))

    def test_clarification_option_selects_table(self):
        res = self.build(request="Создай дашборд по таблице «Техническая поддержка»: про котиков",
                         client=FakeClient('["Обращения по каналам", "Обращения по месяцам", "Обращения всего"]'))
        self.assertEqual(res["status"], "completed")
        self.assertEqual(self.saved()[0]["widgets"][0]["source_table"], "Техническая поддержка")


class ApiTests(unittest.TestCase):
    def wait(self, client, job_id):
        for _ in range(100):
            st = client.get(f"/api/chat/jobs/{job_id}").json()
            if st["status"] in ("completed", "failed", "cancelled"):
                return st
            time.sleep(0.02)
        self.fail("задача не завершилась")

    def test_generate_job_returns_dashboard(self):
        result = {"answer": "Собрал", "status": "completed",
                  "dashboard": {"id": "dash_x", "title": "Дашборд", "widget_count": 3, "failed": []}}
        with patch.object(db, "build_dashboard", return_value=result), TestClient(app) as client:
            r = client.post("/api/dashboards/generate", json={"session_id": "s", "message": "Создай дашборд"})
            self.assertEqual(r.status_code, 202)
            st = self.wait(client, r.json()["job_id"])
        self.assertEqual(st["status"], "completed")
        self.assertEqual(st["result"]["dashboard"]["id"], "dash_x")

    def test_cancel_endpoint(self):
        job = query_tracker.create_job("q", "s")
        with TestClient(app) as client:
            self.assertEqual(client.post(f"/api/chat/jobs/{job.job_id}/cancel").status_code, 204)
            self.assertEqual(client.post("/api/chat/jobs/нет/cancel").status_code, 404)
        self.assertTrue(query_tracker.is_job_cancelled(job.job_id))
        query_tracker.finish_job(job.job_id, query_tracker.ChatResponse(answer="поздно"))
        self.assertEqual(query_tracker.get_job(job.job_id).status, "cancelled")


if __name__ == "__main__":
    unittest.main()
