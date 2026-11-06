"""Tests for Issue #117: Dashboard generation planning.

Updated after merging PR #133: dashboard_generator.py is removed;
functionality lives in backend.app.services.dashboard_builder.
"""
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.app.services.dashboard_builder import (
    catalog_questions,
    dashboard_title,
    request_subject,
)


SALES_TABLE = {
    "name": "data_01_Продажи",
    "columns": [
        {"name": "Дата продажи", "inferred_type": "date"},
        {"name": "Выручка, руб.", "inferred_type": "decimal"},
        {"name": "Количество, шт.", "inferred_type": "integer"},
        {"name": "Канал продажи", "inferred_type": "category", "sample_distinct": 5},
        {"name": "Регион", "inferred_type": "category", "sample_distinct": 10},
    ],
}

SUPPORT_TABLE = {
    "name": "data_02_Поддержка",
    "columns": [
        {"name": "Дата поступления обращения", "inferred_type": "timestamp"},
        {"name": "Дата закрытия обращения", "inferred_type": "timestamp"},
        {"name": "Оценка решения", "inferred_type": "decimal"},
        {"name": "Тип обращения", "inferred_type": "category", "sample_distinct": 7},
    ],
}

DELIVERY_TABLE = {
    "name": "data_04_Доставка",
    "columns": [
        {"name": "Дата отгрузки", "inferred_type": "timestamp"},
        {"name": "Дата доставки", "inferred_type": "timestamp"},
        {"name": "Перевозчик", "inferred_type": "category", "sample_distinct": 8},
        {"name": "Стоимость, руб.", "inferred_type": "decimal"},
    ],
}


class TestDashboardBuilder(unittest.TestCase):

    def test_catalog_questions_sales(self):
        """catalog_questions should produce at least 3 questions for sales table."""
        questions = catalog_questions(SALES_TABLE, "дашборд по продажам")
        self.assertGreaterEqual(len(questions), 3,
                                f"Expected ≥3 questions, got: {questions}")
        joined = " ".join(questions).lower()
        self.assertTrue(any("выручк" in q.lower() or "продаж" in q.lower()
                            for q in questions),
                        "Should mention revenue or sales")

    def test_catalog_questions_support(self):
        """catalog_questions for support table."""
        questions = catalog_questions(SUPPORT_TABLE, "дашборд по поддержке")
        self.assertGreaterEqual(len(questions), 2)

    def test_catalog_questions_delivery(self):
        """catalog_questions for delivery table."""
        questions = catalog_questions(DELIVERY_TABLE, "дашборд по доставке")
        self.assertGreaterEqual(len(questions), 2)
        joined = " ".join(questions).lower()
        self.assertTrue(any("доставк" in q.lower() or "перевозч" in q.lower()
                            for q in questions),
                        "Should mention delivery or carrier")

    def test_request_subject_strips_command_words(self):
        """request_subject removes dashboard-creation command words."""
        subj = request_subject("Создай дашборд по продажам и выручке")
        self.assertNotIn("создай", subj.lower())
        self.assertNotIn("дашборд", subj.lower())
        self.assertIn("продаж", subj.lower())

    def test_dashboard_title_named(self):
        t = dashboard_title("Создай дашборд по продажам", SALES_TABLE)
        self.assertIn("Дашборд", t)
        self.assertLessEqual(len(t), 80)

    def test_dashboard_title_fallback_to_table_name(self):
        t = dashboard_title("Дашборд", SALES_TABLE)
        self.assertIn("Дашборд", t)
        self.assertLessEqual(len(t), 80)


if __name__ == "__main__":
    unittest.main()
