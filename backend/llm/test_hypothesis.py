import datetime as dt
import unittest

from . import hypothesis as hp

TASKS = {"name": "data_03_Задачи_команд", "columns": [
    {"name": "Тип задачи", "inferred_type": "category", "sample_distinct": 8},
    {"name": "Статус", "inferred_type": "category", "sample_distinct": 6},
    {"name": "Создано (МСК)", "inferred_type": "timestamp"},
    {"name": "Начало работы (МСК)", "inferred_type": "timestamp"},
    {"name": "Срок выполнения (МСК)", "inferred_type": "timestamp"},
    {"name": "Завершено (МСК)", "inferred_type": "timestamp"},
    {"name": "Обновлено (МСК)", "inferred_type": "timestamp"},
    {"name": "Плановые трудозатраты, ч", "inferred_type": "decimal"},
    {"name": "Код исполнителя", "inferred_type": "integer"},
]}
SUPPORT = {"name": "data_02", "columns": [
    {"name": "Создано (МСК)", "inferred_type": "timestamp"},
    {"name": "Первый ответ (МСК)", "inferred_type": "timestamp"},
    {"name": "Закрыто (МСК)", "inferred_type": "timestamp"},
]}


class Result:
    def __init__(self, rows):
        self.rows = rows
        self.columns = []
        self.truncated = False


class FakeRunner:
    """Отдаёт строки по фрагменту SQL и запоминает запросы."""

    def __init__(self, answers):
        self.answers = answers
        self.sql = []

    def run(self, table, sql):
        self.sql.append(sql)
        for fragment, rows in self.answers:
            if fragment in sql:
                return Result(rows)
        raise AssertionError(f"неожиданный запрос: {sql}")


class DetectionTests(unittest.TestCase):
    def test_affirm_word_is_not_waiting(self):
        # в «утверждать» есть «ждать»: это был настоящий баг
        d = hp.choose_duration("Можно ли утверждать, что команды стали выполнять задачи быстрее?", TASKS)
        self.assertEqual(d.end, "Завершено (МСК)")

    def test_waiting_means_start_of_work(self):
        d = hp.choose_duration("При каких условиях задачи дольше ждут начала работы?", TASKS)
        self.assertEqual((d.start, d.end), ("Создано (МСК)", "Начало работы (МСК)"))

    def test_named_event_wins(self):
        d = hp.choose_duration("При каких условиях дольше ждут первого ответа?", SUPPORT)
        self.assertEqual(d.end, "Первый ответ (МСК)")

    def test_planned_dates_are_not_events(self):
        d = hp.choose_duration("Стали ли быстрее?", {"name": "x", "columns": [
            {"name": "Принято", "inferred_type": "timestamp"},
            {"name": "Обещанный срок", "inferred_type": "timestamp"}]})
        self.assertIsNone(d)

    def test_ordinary_question_is_not_taken(self):
        runner = FakeRunner([])
        self.assertIsNone(hp.answer("Сколько задач по типам?", TASKS, runner))
        self.assertEqual(runner.sql, [])

    def test_claim_without_time_goes_to_agent(self):
        self.assertIsNone(hp.answer("Правда ли, что продажи выросли?", TASKS, FakeRunner([])))

    def test_period(self):
        latest = dt.date(2026, 9, 30)
        start, end, label = hp._period("в конце сентября 2026 года", latest)
        self.assertEqual((start, end), (dt.date(2026, 9, 21), dt.date(2026, 10, 1)))
        self.assertEqual(label, "конец сентября 2026")
        self.assertEqual(hp._period("в феврале 2025", latest)[:2], (dt.date(2025, 2, 1), dt.date(2025, 3, 1)))
        self.assertEqual(hp._period("в марте", latest)[0].year, 2026)
        self.assertIsNone(hp._period("недавно", latest))


class VerdictTests(unittest.TestCase):
    def test_trap_of_unfinished_tasks(self):
        v = hp.claim_verdict(True, {"base": 60, "period": 44, "base_done": 94, "period_done": 60},
                             {"base": 60, "period": 61}, {"base": 49, "period": 49})
        self.assertFalse(v["confirmed"])
        self.assertTrue(v["censored"])
        self.assertTrue(v["trap"])

    def test_real_speedup_is_confirmed(self):
        v = hp.claim_verdict(True, {"base": 60, "period": 40, "base_done": 94, "period_done": 94},
                             {"base": 60, "period": 40}, {"base": 49, "period": 70})
        self.assertTrue(v["confirmed"])
        self.assertFalse(v["trap"])

    def test_slowdown_is_not_confirmed_as_speedup(self):
        v = hp.claim_verdict(True, {"base": 60, "period": 80, "base_done": 94, "period_done": 94},
                             {"base": 60, "period": 80}, {"base": 49, "period": 30})
        self.assertFalse(v["confirmed"])

    def test_fixed_horizon_must_agree(self):
        v = hp.claim_verdict(True, {"base": 60, "period": 50, "base_done": 94, "period_done": 94},
                             {"base": 60, "period": 50}, {"base": 49, "period": 49})
        self.assertFalse(v["confirmed"])


class DimensionTests(unittest.TestCase):
    def test_small_group_is_noise(self):
        groups = [hp.Group("большая", 10000, 36.0, 19), hp.Group("маленькая", 40, 45.0, 20)]
        self.assertEqual(hp.analyse_dimension(groups, 36.0, 45.0)["notable"], [])

    def test_large_real_gap_is_found(self):
        groups = [hp.Group("обычные", 10000, 37.0, 19), hp.Group("критические", 1700, 4.0, 2)]
        notable = hp.analyse_dimension(groups, 36.0, 45.0)["notable"]
        self.assertEqual([g.value for g, _ in notable], ["критические"])

    def test_tiny_gap_on_huge_sample_is_not_a_condition(self):
        groups = [hp.Group("а", 200000, 36.5, 19), hp.Group("б", 200000, 35.5, 19)]
        self.assertEqual(hp.analyse_dimension(groups, 36.0, 45.0)["notable"], [])

    def test_after_the_fact_columns_are_not_causes(self):
        names = [n for n, _ in hp._dimensions(TASKS, "Создано (МСК)")]
        self.assertIn("Тип задачи", names)
        self.assertNotIn("Статус", names)
        self.assertIn("Месяц", names)

    def test_equal_dimensions_are_merged(self):
        g = hp.Group("x", 100, 5.0, 5)
        found = hp._without_duplicates([{"name": "Город", "notable": [(g, .1)], "strength": 1},
                                        {"name": "Регион", "notable": [(g, .1)], "strength": 1}])
        self.assertEqual([d["name"] for d in found], ["Город / Регион"])


class AnswerTests(unittest.TestCase):
    def claim_runner(self):
        return FakeRunner([
            ("SELECT MAX(", [(dt.datetime(2026, 9, 30, 18), dt.datetime(2026, 9, 30, 23))]),
            ("COUNT(m) AS done", [("base", 1000, 940, 60.0), ("period", 500, 300, 44.0)]),
            ("FILTER (WHERE m <=", [("base", 1000, 49.0), ("period", 300, 49.0)]),
            ("JOIN e", [(dt.datetime(2026, 9, 7), 60.0, 60.0), (dt.datetime(2026, 9, 14), 50.0, 61.0)]),
            ("AS b, ", [("base", 900, 60.0), ("period", 480, 61.0)]),
        ])

    def test_claim_explains_trap_and_shows_all_views(self):
        r = hp.answer("Можно ли утверждать, что в конце сентября 2026 года команды стали выполнять задачи быстрее?",
                      TASKS, self.claim_runner())
        self.assertTrue(r["answer"].startswith("Нет, утверждать это нельзя."))
        self.assertIn("обрываются 30.09.2026", r["answer"])
        self.assertIn("конец сентября 2026", r["answer"])
        self.assertEqual(len(r["table"]["rows"]), 4)
        self.assertEqual(r["chart"]["type"], "line")
        self.assertEqual(r["table_name"], TASKS["name"])
        self.assertIn("SELECT", r["sql"])

    def test_conditions_rank_and_stay_honest(self):
        runner = FakeRunner([
            ("PERCENTILE_CONT(0.75)", [(16.0,)]),
            (">= 16.0 AS big", [(True, 1000, 36.6, 19.7), (False, 3000, 35.3, 19.0)]),
            ("STDDEV_SAMP", [(100000, 36.0, 45.0, 19.0)]),
            ('"Тип задачи"::text', [("Согласование", 6000, 40.0, 21.0), ("Разработка", 94000, 35.7, 19.0)]),
            ("EXTRACT(MONTH", [("12", 8000, 42.0, 22.0), ("6", 92000, 35.5, 19.0)]),
            ("EXTRACT(ISODOW", [("1", 50000, 36.1, 19.0), ("2", 50000, 35.9, 19.0)]),
        ])
        r = hp.answer("При каких условиях крупные задачи дольше ждут начала работы?", TASKS, runner)
        self.assertIn("Сам размер почти ни при чём", r["answer"])
        self.assertIn("Месяц: декабрь — 42,0 ч", r["answer"])
        self.assertIn("Без заметной разницы: «День недели»", r["answer"])
        self.assertIn("не доказывает причину", r["answer"])
        self.assertTrue(all("SELECT" in q and "UPDATE" not in q for q in runner.sql))

    def test_short_durations_are_in_minutes(self):
        runner = FakeRunner([
            ("STDDEV_SAMP", [(100000, 0.05, 0.05, 0.04)]),
            ('"Тип задачи"::text', [("Долгие", 5000, 0.1, 0.09), ("Короткие", 95000, 0.047, 0.04)]),
            ("EXTRACT(MONTH", [("1", 50000, 0.05, 0.04), ("2", 50000, 0.05, 0.04)]),
            ("EXTRACT(ISODOW", [("1", 50000, 0.05, 0.04), ("2", 50000, 0.05, 0.04)]),
        ])
        r = hp.answer("Почему задачи дольше ждут начала работы?", TASKS, runner)
        self.assertIn("Долгие — 6,0 мин", r["answer"])
        self.assertIn("среднее 3,0 мин", r["answer"])

    def test_too_little_data_goes_to_agent(self):
        runner = FakeRunner([("STDDEV_SAMP", [(5, 36.0, 45.0, 19.0)])])
        self.assertIsNone(hp.answer("При каких условиях задачи дольше ждут начала работы?", TASKS, runner))


if __name__ == "__main__":
    unittest.main()
