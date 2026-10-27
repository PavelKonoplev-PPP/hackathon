"""Бизнес-словарь в агенте (BI-37 / #62) — без модели и БД."""
import json
import os
import tempfile
import time
import unittest
from pathlib import Path

from backend.llm import glossary as gl
from backend.llm.ask import _SESSIONS, answer_question, build_sql_messages, choose_table
from backend.llm.semantics import Intent, check_sql, detect_out_of_scope
from backend.llm.test_ask import CATALOG, FakeRunner, ScriptedClient

REAL_CATALOG = Path(__file__).resolve().parents[2] / "data" / "schema-catalog.json"


def entry(table, column, status="approved", description="", synonyms=(), schema="raw"):
    return {"schema": schema, "table": table, "column": column, "status": status,
            "description": description, "synonyms": list(synonyms), "label": column, "unit": None}


class GlossaryFile:
    """Временный data/business-glossary.json и BUSINESS_GLOSSARY_PATH на время теста."""

    def __init__(self, test: unittest.TestCase, entries: list[dict]):
        self.dir = tempfile.TemporaryDirectory()
        test.addCleanup(self.dir.cleanup)
        self.path = Path(self.dir.name) / "business-glossary.json"
        self.write(entries)
        old = os.environ.get("BUSINESS_GLOSSARY_PATH")
        os.environ["BUSINESS_GLOSSARY_PATH"] = str(self.path)
        test.addCleanup(lambda: os.environ.pop("BUSINESS_GLOSSARY_PATH", None) if old is None
                        else os.environ.__setitem__("BUSINESS_GLOSSARY_PATH", old))
        gl._CACHE.clear()
        test.addCleanup(gl._CACHE.clear)

    def write(self, entries):
        self.path.write_text(json.dumps({"version": 1, "schema": "raw", "entries": entries},
                                        ensure_ascii=False), encoding="utf-8")


CAT = {**CATALOG, "schema": "raw"}


class LoadTests(unittest.TestCase):
    def test_only_approved_entries_are_trusted(self):
        GlossaryFile(self, [
            entry("data_01_Продажи", "Канал продажи", description="Где оформлен заказ",
                  synonyms=["точка продаж"]),
            entry("data_01_Продажи", "Город", status="proposed", description="догадка"),
            entry("data_01_Продажи", "Тип операции", status="needs_review", description="старое"),
        ])
        trusted = gl.load()
        self.assertEqual(list(trusted), [("raw", "data_01_Продажи", "Канал продажи")])
        self.assertEqual(trusted[("raw", "data_01_Продажи", "Канал продажи")]["synonyms"],
                         ["точка продаж"])

    def test_missing_or_broken_file_is_ignored(self):
        f = GlossaryFile(self, [])
        f.path.unlink()
        self.assertEqual(gl.load(), {})
        f.path.write_text("{не json", encoding="utf-8")
        self.assertEqual(gl.load(), {})
        self.assertIs(gl.apply(CAT), CAT)

    def test_file_is_reread_after_change(self):
        f = GlossaryFile(self, [])
        self.assertEqual(gl.load(), {})
        f.write([entry("data_01_Продажи", "Город", synonyms=["населённый пункт"])])
        later = time.time() + 5
        os.utime(f.path, (later, later))
        self.assertIn(("raw", "data_01_Продажи", "Город"), gl.load())

    def test_apply_matches_schema_and_does_not_mutate(self):
        trusted = {("raw", "data_01_Продажи", "Город"): {"description": "d", "synonyms": [], "unit": None}}
        applied = gl.apply(CAT, trusted)
        city = next(c for c in applied["tables"][0]["columns"] if c["name"] == "Город")
        self.assertEqual(city["glossary"]["description"], "d")
        self.assertNotIn("glossary", next(c for c in CAT["tables"][0]["columns"] if c["name"] == "Город"))
        self.assertIs(gl.apply({**CAT, "schema": "public"}, trusted)["tables"], CAT["tables"])


class AgentUsesGlossaryTests(unittest.TestCase):
    def setUp(self):
        _SESSIONS.clear()

    def test_prompt_has_definition_and_synonyms(self):
        trusted = {("raw", "data_01_Продажи", "Канал продажи"): {
            "description": "Где оформлен заказ", "synonyms": ["точка продаж"], "unit": None}}
        table = gl.apply(CAT, trusted)["tables"][0]
        prompt = build_sql_messages("Выручка по точкам продаж", table, {})[0]["content"]
        self.assertIn('"Канал продажи" — категория (текст); смысл: Где оформлен заказ; '
                      'в вопросах называют: «точка продаж»', prompt)
        self.assertIn('речь о колонках "Канал продажи"', prompt)

    def test_synonym_counts_as_named_column(self):
        q = "Сколько кликов в каждой рекламной сети?"
        sql = 'SELECT "Площадка", SUM("Клики") FROM t GROUP BY "Площадка"'
        cols = ["Площадка", "Клики", "Город"]
        without = check_sql(sql, q, Intent.parse(q), {}, numeric_columns=["Клики"], columns=cols)
        self.assertIn("Лишняя группировка", " ".join(without))
        with_alias = check_sql(sql, q, Intent.parse(q), {}, numeric_columns=["Клики"], columns=cols,
                               aliases={"Площадка": ["рекламная сеть"]})
        self.assertEqual(with_alias, [])

    def test_end_to_end_with_glossary_file(self):
        GlossaryFile(self, [entry("data_01_Продажи", "Канал продажи",
                                  description="Где оформлен заказ", synonyms=["точка продаж"])])
        client = ScriptedClient(['```sql\nSELECT "Канал продажи", SUM("Цена, руб.") AS "Выручка" '
                                 'FROM t GROUP BY 1\n```', "Готово: 1 500 000,50 и 900 000."])
        trace = {}
        answer_question("Какая выручка по каждой точке продаж?", CAT, FakeRunner(), client=client,
                        trace=trace)
        self.assertEqual(trace["errors"], [])
        self.assertIn("смысл: Где оформлен заказ", client.calls[0][0]["content"])


class OutOfScopeTests(unittest.TestCase):
    """«Нет метрики CSAT» — пока термин не определён в словаре; после approve — считаем."""

    def setUp(self):
        _SESSIONS.clear()

    def test_defined_term_overrides_refusal(self):
        cat = {"schema": "raw", "tables": [{"name": "data_02_Поддержка", "columns": [
            {"name": "Канал обращения", "inferred_type": "category", "conversion": {}},
            {"name": "Оценка клиента", "inferred_type": "integer",
             "conversion": {"target_type": "integer", "policy": "safe_complete"}}]}]}
        q = "Какой средний CSAT по каналам?"
        sql = ('```sql\nSELECT "Канал обращения", AVG("Оценка клиента") AS "CSAT" FROM t '
               'GROUP BY 1\n```')
        runner = FakeRunner(rows=[("Чат", 4.1), ("Телефон", 3.9)], columns=["Канал обращения", "CSAT"])

        f = GlossaryFile(self, [])  # словарь пуст: не зависим от data/ на машине разработчика
        before = answer_question(q, cat, runner, client=ScriptedClient([]), trace={})
        self.assertIn("нет метрики NPS", before["answer"])

        f.write([entry("data_02_Поддержка", "Оценка клиента",
                                  description="Оценка обращения от 1 до 5", synonyms=["CSAT"])])
        later = time.time() + 5
        os.utime(f.path, (later, later))
        self.assertIsNone(detect_out_of_scope(q))  # этой же проверкой пользуется бэкенд
        trace = {}
        after = answer_question(q, cat, runner, client=ScriptedClient([sql, "Чат 4,1, телефон 3,9."]),
                                trace=trace)
        self.assertEqual(trace["errors"], [])
        self.assertEqual(after["table"]["rows"][0], ["Чат", "4,10"])
        # «NPS» словарь не определял — по-прежнему честный отказ
        nps = answer_question("Какой NPS по каналам?", cat, runner, client=ScriptedClient([]), trace={})
        self.assertIn("нет метрики NPS", nps["answer"])


@unittest.skipUnless(REAL_CATALOG.is_file(), "нет data/schema-catalog.json")
class TableChoiceTests(unittest.TestCase):
    def test_synonym_selects_table(self):
        catalog = json.loads(REAL_CATALOG.read_text(encoding="utf-8"))
        q = "Какой средний CSAT по каналам?"
        table, candidates = choose_table(q, catalog["tables"], {})
        self.assertIsNone(table)  # без словаря непонятно, о какой таблице речь
        trusted = {("raw", "data_02_Техническая_поддержка", "Оценка клиента"): {
            "description": "Оценка обращения клиентом от 1 до 5", "synonyms": ["CSAT"], "unit": None}}
        table, _ = choose_table(q, gl.apply(catalog, trusted)["tables"], {})
        self.assertEqual(table["name"], "data_02_Техническая_поддержка")


if __name__ == "__main__":
    unittest.main()
