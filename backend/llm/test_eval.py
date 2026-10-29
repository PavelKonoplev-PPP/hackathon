"""Тесты сравнения результатов и статическая проверка эталонов (без БД и модели)."""
import datetime as dt
import json
import unittest
from decimal import Decimal
from pathlib import Path

from backend.llm.eval import ApiAsker, compare, parse_cell, run_eval, table_from_sql
from backend.llm.eval_cases import CASES
from backend.llm.eval_holdout import HOLDOUT
from backend.llm.sql_guard import validate_sql

CATALOG = Path(__file__).resolve().parents[2] / "data" / "schema-catalog.json"
VALUES = CATALOG.with_name("category-values.json")  # выгрузка значений категорий (см. README)


class CompareTests(unittest.TestCase):
    def test_value(self):
        self.assertTrue(compare("value", [(500000,)], [("Всего", 500000)])[0])
        self.assertFalse(compare("value", [(500000,)], [(499000,)])[0])
        self.assertFalse(compare("value", [(500000,)], [(499999,)])[0])
        self.assertTrue(compare("value", [(Decimal("3.9412"),)], [(Decimal("3.94"),)])[0])
        self.assertFalse(compare("value", [(Decimal("3.9412"),)], [(Decimal("3.9"),)])[0])

    def test_all_ignores_order_names_and_rounding(self):
        gold = [("Онлайн", Decimal("10.004")), ("Магазин", Decimal("5"))]
        agent = [("магазин", 5.0), ("Онлайн", 10.0)]
        self.assertTrue(compare("all", gold, agent)[0])

    def test_all_reports_missing(self):
        ok, reason = compare("all", [("A", 1), ("B", 2)], [("A", 1)])
        self.assertFalse(ok)
        self.assertIn("(b, 2)", reason)

    def test_wrong_number(self):
        self.assertFalse(compare("all", [("A", 100)], [("A", 90)])[0])

    def test_top_order_matters(self):
        gold = [("A", 3), ("B", 2)]
        self.assertTrue(compare("top", gold, [("A", 3), ("B", 2), ("C", 1)])[0])
        self.assertFalse(compare("top", gold, [("B", 2), ("A", 3)])[0])

    def test_dates_and_percent(self):
        gold = [(dt.datetime(2025, 3, 1), 7)]
        self.assertTrue(compare("all", gold, [(dt.date(2025, 3, 1), 7)])[0])
        self.assertTrue(compare("all", [("A", 0.0123)], [("A", 1.23)], scale100=True)[0])
        self.assertFalse(compare("all", [("A", 0.0123)], [("A", 1.23)])[0])

    def test_truncated_answer(self):
        gold = [(f"Команда {i}", i) for i in range(250)]
        shown = gold[50:]  # агент показал 200 строк из 250
        self.assertTrue(compare("all", gold, shown)[0])
        broken = shown[:-1] + [("Команда 249", 1)]
        ok, reason = compare("all", gold, broken)
        self.assertFalse(ok)
        self.assertIn("обрезан до 200 строк из 250", reason)
        self.assertFalse(compare("all", gold, shown[:150])[0])  # не обрезан — нужны все строки

    def test_empty(self):
        self.assertFalse(compare("value", [(None,)], [(1,)])[0])
        self.assertFalse(compare("all", [("A", 1)], [])[0])


class ApiParsingTests(unittest.TestCase):
    """Сквозной режим (#34): числа и даты из таблицы ответа API."""

    def test_parse_cell(self):
        self.assertEqual(parse_cell("1 234 567"), 1234567)
        self.assertAlmostEqual(parse_cell("1 234,56"), 1234.56)
        self.assertAlmostEqual(parse_cell("+20,0%"), 20.0)
        self.assertEqual(parse_cell("-5"), -5)
        self.assertEqual(parse_cell("01.09.2025"), dt.date(2025, 9, 1))
        self.assertEqual(parse_cell("01.09.2025 14:30"), dt.datetime(2025, 9, 1, 14, 30))
        self.assertIsNone(parse_cell("—"))
        self.assertEqual(parse_cell("Интернет-магазин"), "Интернет-магазин")
        self.assertEqual(parse_cell("VK Реклама"), "VK Реклама")
        self.assertEqual(parse_cell(7), 7)

    def test_table_from_sql(self):
        sql = "-- t = данные таблицы «Продажи» с приведёнными типами\nSELECT COUNT(*) FROM t"
        self.assertEqual(table_from_sql(sql), "Продажи")
        self.assertIsNone(table_from_sql(None))

    def test_rounded_numbers_from_api(self):
        gold = [("A", Decimal("0.0345")), ("B", Decimal("3.9412"))]
        api_rows = [("A", parse_cell("0,03")), ("B", parse_cell("3,94"))]
        self.assertFalse(compare("all", gold, api_rows)[0])
        self.assertTrue(compare("all", gold, api_rows, rounded=True)[0])
        self.assertFalse(compare("all", [("A", Decimal("0.5"))], [("A", 0.4)], rounded=True)[0])


class FakeApi:
    """Минимальный HTTP-сервер с /api/health, /api/chat и /api/chat/jobs."""

    def __init__(self, reply):
        import http.server
        import threading

        self.reply, self.messages, self.jobs = reply, [], {}
        api = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _send(self, code, obj):
                data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                if self.path == "/api/health":
                    return self._send(200, {"status": "ok"})
                job_id = self.path.rsplit("/", 1)[-1]
                job = api.jobs.get(job_id)
                if job is None:
                    return self._send(404, {"detail": "not found"})
                job["polls"] += 1
                if job["polls"] < 2:
                    return self._send(200, {"job_id": job_id, "status": "running"})
                return self._send(200, {"job_id": job_id, "status": "completed", "result": job["result"]})

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                api.messages.append((body["session_id"], body["message"]))
                result = api.reply(body["message"])
                if self.path == "/api/chat":
                    return self._send(200, result)
                job_id = f"job{len(api.jobs)}"
                api.jobs[job_id] = {"result": result, "polls": 0}
                return self._send(202, {"job_id": job_id, "status": "pending"})

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class GoldRunner:
    def __init__(self, rows):
        self._rows = rows

    def run(self, table, sql):
        from types import SimpleNamespace
        return SimpleNamespace(rows=self._rows)


class ApiRunTests(unittest.TestCase):
    CATALOG = {"tables": [{"name": "data_01_Продажи", "columns": []}]}

    @staticmethod
    def reply(message):
        if "погода" in message:
            return {"answer": "Я отвечаю только на вопросы по данным.", "status": "refused"}
        if "статус" in message:  # обычный ответ без таблицы — это не отказ
            return {"answer": "Готово, изменил статус.", "status": "completed"}
        if "без источника" in message:  # данные есть, а таблица-источник не указана
            return {"answer": "Готово", "table": {"columns": ["Канал продажи", "Выручка"],
                                                  "rows": [["Онлайн", "1 234 567,89"], ["Магазин", "100"]]}}
        if "без карты" in message:
            return {"answer": "Уточните", "clarification": {"question": "Что считать?",
                                                            "options": ["Строки", "Заказы"]}}
        return {"answer": "Готово", "status": "completed", "table_name": "data_01_Продажи",
                "sql": "-- t = данные таблицы «Продажи» с приведёнными типами\nSELECT …",
                "table": {"columns": ["Канал продажи", "Выручка"],
                          "rows": [["Онлайн", "1 234 567,89"], ["Магазин", "100"]]}}

    def setUp(self):
        self.api = FakeApi(self.reply)
        self.addCleanup(self.api.close)

    def run_cases(self, cases, **kw):
        asker = ApiAsker(self.api.url, poll=0.01, **kw)
        asker.check()
        runner = GoldRunner([("Онлайн", Decimal("1234567.891")), ("Магазин", 100)])
        return run_eval(cases, self.CATALOG, runner, asker=asker, log=lambda *_: None)

    def test_jobs_and_modes(self):
        cases = [
            dict(id="a", table="data_01_Продажи", mode="all", question="Выручка по каналам",
                 gold="SELECT 1"),
            dict(id="b", table="data_01_Продажи", mode="clarify", clarify="unit",
                 question="Сколько продаж без карты?"),
            dict(id="c", table=None, mode="refuse", question="Какая погода?"),
            dict(id="d", table="data_02_Техническая_поддержка", mode="all",
                 question="Выручка по каналам", gold="SELECT 1"),
        ]
        self.CATALOG = {"tables": self.CATALOG["tables"] + [{"name": cases[3]["table"], "columns": []}]}
        report = self.run_cases(cases)
        self.assertEqual([r["ok"] for r in report], [True, True, True, False])
        self.assertIn("не та таблица: data_01_Продажи", report[3]["reason"])

    def test_false_positives_rejected(self):
        """Ревью #88: ответ без таблицы — не отказ; данные без table_name — провал."""
        cases = [
            dict(id="r", table=None, mode="refuse", question="Поменяй статус всех заявок"),
            dict(id="t", table="data_01_Продажи", mode="all", question="Выручка без источника",
                 gold="SELECT 1"),
        ]
        report = self.run_cases(cases)
        self.assertEqual([r["ok"] for r in report], [False, False])
        self.assertEqual(report[0]["reason"], "агент не отказался")
        self.assertIn("table_name", report[1]["reason"])

    def test_dialog_uses_one_session_and_sync_endpoint(self):
        case = dict(id="dlg", table="data_01_Продажи", mode="all", question="А за сентябрь?",
                    steps=["Выручка по каналам", "А за сентябрь?"], gold="SELECT 1")
        report = self.run_cases([case], use_jobs=False)
        self.assertTrue(report[0]["ok"], report[0])
        sessions = {s for s, _ in self.api.messages}
        self.assertEqual(len(sessions), 1)
        self.assertTrue(next(iter(sessions)).startswith("eval-dlg-"))
        self.assertEqual(self.api.jobs, {})

    def test_api_down(self):
        asker = ApiAsker("http://127.0.0.1:9", timeout=2)
        with self.assertRaisesRegex(RuntimeError, "API недоступен"):
            asker.check()


@unittest.skipUnless(CATALOG.is_file(), "нет data/schema-catalog.json")
class GoldStaticTests(unittest.TestCase):
    """Каждый эталон ссылается на существующие таблицу и колонки и проходит проверку SQL."""

    def test_gold_sql(self):
        tables = {t["name"]: t for t in json.loads(CATALOG.read_text(encoding="utf-8"))["tables"]}
        ids = set()
        for case in CASES + HOLDOUT:
            with self.subTest(case=case["id"]):
                self.assertNotIn(case["id"], ids)
                ids.add(case["id"])
                self.assertIn(case["mode"], ("value", "all", "top", "clarify", "refuse"))
                if case["mode"] == "refuse":
                    continue
                self.assertIn(case["table"], tables)
                if "gold" in case:
                    columns = [c["name"] for c in tables[case["table"]]["columns"]]
                    validate_sql(case["gold"], columns)
                elif case["mode"] != "clarify":
                    self.assertIsInstance(case["expected"], int)



@unittest.skipUnless(CATALOG.is_file() and VALUES.is_file(), "нет каталога или выгрузки значений")
class TableChoiceTests(unittest.TestCase):
    """Выбор таблицы / уточнение / отказ для всех вопросов набора — без модели."""

    def test_all_cases(self):
        from backend.llm.ask import WRITE_INTENT, choose_table, unit_options
        from backend.llm.semantics import Intent

        tables = json.loads(CATALOG.read_text(encoding="utf-8"))["tables"]
        values = json.loads(VALUES.read_text(encoding="utf-8"))
        for case in CASES:
            question = (case.get("steps") or [case["question"]])[0]
            if case["mode"] == "refuse":
                question = case["question"]  # в диалоге отказ проверяется на последнем вопросе
            with self.subTest(case=case["id"]):
                if WRITE_INTENT.search(question):
                    self.assertEqual(case["mode"], "refuse")
                    continue
                table, candidates = choose_table(question, tables, values)
                if case["mode"] == "refuse":
                    self.assertEqual((table, candidates), (None, []))
                elif case["mode"] == "clarify" and case["clarify"] == "table":
                    self.assertIsNone(table)
                    self.assertGreater(len(candidates), 1)
                else:
                    self.assertIsNotNone(table, candidates)
                    self.assertEqual(table["name"], case["table"])
                    units = unit_options(question, Intent.parse(question), table)
                    if case["mode"] == "clarify":
                        self.assertTrue(units)
                    elif not case.get("steps"):
                        self.assertFalse(units)

    def test_semantic_checks_accept_gold_sql(self):
        """Эталонный SQL верен по определению — смысловая проверка не должна к нему придираться."""
        from backend.llm.catalog_summary import display_table_name
        from backend.llm.semantics import Intent, adjust_sql, check_sql, words
        from backend.llm.sql_runner import column_kind

        tables = {t["name"]: t for t in json.loads(CATALOG.read_text(encoding="utf-8"))["tables"]}
        values = json.loads(VALUES.read_text(encoding="utf-8"))
        for case in CASES + HOLDOUT:
            if "gold" not in case:
                continue
            with self.subTest(case=case["id"]):
                table = tables[case["table"]]
                question = " ".join(case.get("steps") or [case["question"]])
                intent = Intent.parse(question)
                sql = adjust_sql(case["gold"], intent)
                problems = check_sql(
                    sql, question, intent, values[table["name"]],
                    ignore=words(display_table_name(table["name"])),
                    numeric_columns=[c["name"] for c in table["columns"]
                                     if column_kind(c) in ("integer", "decimal")],
                    columns=[c["name"] for c in table["columns"]])
                self.assertEqual(problems, [])


if __name__ == "__main__":
    unittest.main()
