"""Unit tests for advanced analytics modules (Scenarios 7-10)."""

import unittest
from backend.llm import advanced_analytics as adv


class TestAdvancedAnalytics(unittest.TestCase):
    def test_seasonality_detection(self) -> None:
        self.assertTrue(adv.is_seasonality("Какая сезонность продаж по месяцам года?"))
        self.assertTrue(adv.is_seasonality("Есть ли сезонные пики зимой?"))
        self.assertTrue(adv.is_seasonality("Какой паттерн повторяется каждый год?"))
        self.assertTrue(adv.is_seasonality("Покажи периодические колебания выручки"))
        self.assertFalse(adv.is_seasonality("Сколько заказов было в мае?"))

    def test_seasonality_analysis_and_summary(self) -> None:
        columns = ["Номер месяца", "Выручка, руб."]
        rows = [
            [1, 100000],
            [2, 80000],   # trough (-20%)
            [3, 100000],
            [4, 95000],
            [5, 105000],
            [6, 100000],
            [7, 98000],
            [8, 102000],
            [9, 101000],
            [10, 100000],
            [11, 105000],
            [12, 140000], # peak (+40%)
        ]
        res = adv.analyze_seasonality(columns, rows)
        self.assertIsNotNone(res)
        self.assertEqual(res["max"][0], "Декабрь")
        self.assertEqual(res["min"][0], "Февраль")
        self.assertTrue(len(res["peaks"]) >= 1)
        self.assertTrue(len(res["troughs"]) >= 1)

        summary = adv.seasonality_summary(res)
        self.assertIn("Декабрь", summary)
        self.assertIn("Февраль", summary)
        self.assertIn("Пиковый период", summary)

    def test_segmentation_detection_and_summary(self) -> None:
        self.assertTrue(adv.is_segmentation("Как группируются пользователи по сегментам?"))
        self.assertTrue(adv.is_segmentation("Что отличает золотых клиентов от базовых?"))
        self.assertTrue(adv.is_segmentation("Сделай профиль когорты покупателей"))
        self.assertFalse(adv.is_segmentation("Какая общая выручка?"))

        columns = ["Сегмент", "Выручка, руб.", "Число заказов"]
        rows = [
            ["Золотой", 1500000, 350],
            ["Серебряный", 900000, 420],
            ["Базовый", 400000, 500],
        ]
        summary = adv.segmentation_summary(columns, rows)
        self.assertIn("Золотой", summary)
        self.assertIn("Серебряный", summary)
        self.assertIn("Базовый", summary)
        self.assertIn("Сравнительный анализ сегментов", summary)

    def test_correlation_calculation_and_summary(self) -> None:
        self.assertTrue(adv.is_correlation("Есть ли корреляция между ценой и продажами?"))
        self.assertTrue(adv.is_correlation("Какая взаимосвязь между скидкой и объемом?"))
        self.assertTrue(adv.is_correlation("Изменения в рекламе предшествуют росту продаж на 5 дней?"))
        self.assertFalse(adv.is_correlation("Сколько продаж в Москве?"))

        # Perfect positive correlation
        x = [1.0, 2.0, 3.0, 4.0, 5.0]
        y = [2.0, 4.0, 6.0, 8.0, 10.0]
        r = adv.calculate_pearson(x, y)
        self.assertAlmostEqual(r, 1.0, places=4)

        summary_pos = adv.correlation_summary(r, "Скидка", "Количество")
        self.assertIn("сильная статистическая взаимосвязь", summary_pos)
        self.assertIn("прямая (положительная)", summary_pos)

        # Perfect negative correlation
        y_neg = [10.0, 8.0, 6.0, 4.0, 2.0]
        r_neg = adv.calculate_pearson(x, y_neg)
        self.assertAlmostEqual(r_neg, -1.0, places=4)

        summary_neg = adv.correlation_summary(r_neg, "Цена", "Спрос")
        self.assertIn("обратная (отрицательная)", summary_neg)

    def test_forecasting_extrapolation_and_summary(self) -> None:
        self.assertTrue(adv.is_forecasting("Какой прогноз выручки на следующий квартал?"))
        self.assertTrue(adv.is_forecasting("Экстраполяция продаж при текущих условиях"))
        self.assertTrue(adv.is_forecasting("Что если увеличить средний чек?"))
        self.assertFalse(adv.is_forecasting("Какая выручка за прошлый год?"))

        historical = [100.0, 110.0, 120.0, 130.0]
        projections = adv.linear_extrapolation(historical, steps_ahead=3)
        self.assertEqual(len(projections), 3)
        self.assertAlmostEqual(projections[0], 140.0)
        self.assertAlmostEqual(projections[1], 150.0)
        self.assertAlmostEqual(projections[2], 160.0)

        hist_labeled = [("Q1", 100.0), ("Q2", 110.0), ("Q3", 120.0), ("Q4", 130.0)]
        summary = adv.forecast_summary(hist_labeled, projections)
        self.assertIn("экстраполяции исторического тренда", summary)
        self.assertIn("160", summary)
        self.assertIn("Примечание", summary)


if __name__ == "__main__":
    unittest.main()
