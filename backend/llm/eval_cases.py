"""Набор вопросов с эталонным SQL для тестов аналитической точности (issue #6).

Эталон пишется вручную к той же таблице `t` (типы уже приведены), что видит агент.
Режимы сравнения:
- "value" — один ответ-число: оно должно найтись в результате агента;
- "all"   — все строки эталона должны быть в результате агента (порядок не важен);
- "top"   — первые N строк агента должны совпасть с эталоном по порядку;
- "clarify" — агент должен задать уточняющий вопрос (clarify="table" | "unit").
scale100=True — допускается ответ в процентах (значение × 100).
expected=число — эталон задан числом, подтверждённым командой БД, вместо SQL.
steps=[...] — диалог: вопросы задаются по очереди в одной сессии, проверяется последний.

Добавляя кейс, проверь эталон: python -m backend.llm.eval --gold-only
"""

SALES = "data_01_Продажи"
SUPPORT = "data_02_Техническая_поддержка"
TASKS = "data_03_Задачи_команд"
DELIVERY = "data_04_Доставка"
EVENTS = "data_05_События_приложения"
ADS = "data_06_Рекламные_кампании"
PURCHASE = "data_07_Заявки_на_закупку"
JOBS = "data_08_Фоновые_задания"
DRUGS = "data_09_Лекарственные_препараты"

REVENUE = ('SUM("Количество, шт." * "Цена до скидки за единицу, руб." '
           '* (1 - "Скидка, %" / 100.0))')

CASES = [
    # --- Продажи: COUNT, COUNT DISTINCT, SUM, AVG, MAX, GROUP BY, LIMIT, время ---
    dict(id="sales_count", table=SALES, mode="value",
         question="Сколько всего записей в данных о продажах?",
         gold='SELECT COUNT(*) FROM t'),
    dict(id="sales_distinct_buyers", table=SALES, mode="value",
         question="Сколько уникальных покупателей в продажах?",
         gold='SELECT COUNT(DISTINCT "Код покупателя") FROM t'),
    dict(id="sales_distinct_orders", table=SALES, mode="value",
         question="Сколько разных заказов в продажах?",
         gold='SELECT COUNT(DISTINCT "Код заказа") FROM t'),
    dict(id="sales_qty_by_channel", table=SALES, mode="all",
         question="Сколько штук товаров продано по каждому каналу продажи?",
         gold='SELECT "Канал продажи", SUM("Количество, шт.") FROM t GROUP BY 1'),
    dict(id="sales_revenue_by_channel", table=SALES, mode="all",
         question="Какая выручка с учётом скидки по каналам продаж?",
         gold=f'SELECT "Канал продажи", {REVENUE} FROM t GROUP BY 1'),
    dict(id="sales_avg_discount_by_category", table=SALES, mode="all",
         question="Какая средняя скидка в процентах по категориям товаров?",
         gold='SELECT "Категория товара", AVG("Скидка, %") FROM t GROUP BY 1'),
    dict(id="sales_top5_cities_qty", table=SALES, mode="top",
         question="Топ-5 городов по количеству проданных штук",
         gold='SELECT "Город", SUM("Количество, шт.") FROM t GROUP BY 1 ORDER BY 2 DESC LIMIT 5'),
    dict(id="sales_returns_count", table=SALES, mode="value",
         question="Сколько было операций возврата?",
         gold='SELECT COUNT(*) FROM t WHERE "Тип операции" = \'Возврат\''),
    dict(id="sales_count_by_month", table=SALES, mode="all",
         question="Сколько записей о продажах было в каждом месяце?",
         gold='SELECT date_trunc(\'month\', "Дата операции (МСК)"), COUNT(*) FROM t GROUP BY 1'),
    dict(id="sales_max_price", table=SALES, mode="value",
         question="Какая максимальная цена до скидки за единицу товара?",
         gold='SELECT MAX("Цена до скидки за единицу, руб.") FROM t'),
    dict(id="sales_distinct_products_by_category", table=SALES, mode="all",
         question="Сколько разных товаров продавалось в каждой категории?",
         gold='SELECT "Категория товара", COUNT(DISTINCT "Товар") FROM t GROUP BY 1'),

    # --- Техническая поддержка ---
    dict(id="support_by_channel", table=SUPPORT, mode="all",
         question="Сколько обращений в техподдержку по каждому каналу обращения?",
         gold='SELECT "Канал обращения", COUNT(*) FROM t GROUP BY 1'),
    dict(id="support_avg_rating_by_channel", table=SUPPORT, mode="all",
         question="Какая средняя оценка клиента по каналам обращения?",
         gold='SELECT "Канал обращения", AVG("Оценка клиента") FROM t GROUP BY 1'),
    dict(id="support_by_priority", table=SUPPORT, mode="all",
         question="Сколько обращений в поддержку по каждому приоритету?",
         gold='SELECT "Приоритет", COUNT(*) FROM t GROUP BY 1'),
    dict(id="support_avg_hours_to_close", table=SUPPORT, mode="value",
         question="Сколько в среднем часов проходит от создания обращения до его закрытия?",
         gold='SELECT AVG(EXTRACT(EPOCH FROM ("Закрыто (МСК)" - "Создано (МСК)")) / 3600) FROM t'),

    # --- Доставка ---
    dict(id="delivery_by_carrier", table=DELIVERY, mode="all",
         question="Сколько отправлений у каждого перевозчика?",
         gold='SELECT "Перевозчик", COUNT(*) FROM t GROUP BY 1'),
    dict(id="delivery_avg_cost_by_method", table=DELIVERY, mode="all",
         question="Какая средняя стоимость доставки по способам доставки?",
         gold='SELECT "Способ доставки", AVG("Стоимость доставки, руб.") FROM t GROUP BY 1'),
    dict(id="delivery_total_weight", table=DELIVERY, mode="value",
         question="Какой общий вес всех отправлений в килограммах?",
         gold='SELECT SUM("Вес, кг") FROM t'),

    # --- Реклама ---
    dict(id="ads_spend_by_platform", table=ADS, mode="all",
         question="Сколько потратили на рекламу по площадкам?",
         gold='SELECT "Площадка", SUM("Расходы, руб.") FROM t GROUP BY 1'),
    dict(id="ads_total_clicks", table=ADS, mode="value",
         question="Сколько всего было кликов по рекламе?",
         gold='SELECT SUM("Клики") FROM t'),
    dict(id="ads_ctr_by_platform", table=ADS, mode="all", scale100=True,
         question="Какой CTR (отношение кликов к показам) по каждой рекламной площадке?",
         gold='SELECT "Площадка", SUM("Клики")::numeric / NULLIF(SUM("Показы"), 0) FROM t GROUP BY 1'),

    # --- События приложения ---
    dict(id="events_by_platform", table=EVENTS, mode="all",
         question="Сколько событий приложения на каждой платформе?",
         gold='SELECT "Платформа", COUNT(*) FROM t GROUP BY 1'),
    dict(id="events_distinct_users", table=EVENTS, mode="value",
         question="Сколько уникальных пользователей в событиях приложения?",
         gold='SELECT COUNT(DISTINCT "Код пользователя") FROM t'),

    # --- Заявки на закупку ---
    dict(id="purchase_by_status", table=PURCHASE, mode="all",
         question="Сколько заявок на закупку в каждом статусе?",
         gold='SELECT "Статус", COUNT(*) FROM t GROUP BY 1'),
    dict(id="purchase_total_requested", table=PURCHASE, mode="value",
         question="Какая общая запрошенная сумма по всем заявкам на закупку?",
         gold='SELECT SUM("Запрошенная сумма, руб.") FROM t'),

    # --- Фоновые задания ---
    dict(id="jobs_by_status", table=JOBS, mode="all",
         question="Сколько фоновых заданий в каждом статусе?",
         gold='SELECT "Статус", COUNT(*) FROM t GROUP BY 1'),
    dict(id="jobs_total_processed", table=JOBS, mode="value",
         question="Сколько всего записей обработали фоновые задания?",
         gold='SELECT SUM("Обработано записей") FROM t'),

    # --- Лекарственные препараты ---
    dict(id="drugs_units_by_type", table=DRUGS, mode="all",
         question="Сколько единиц лекарств купили по каждому типу лекарства?",
         gold='SELECT "Тип_Лекарства", SUM("Сколько_Купили") FROM t GROUP BY 1'),
    dict(id="drugs_distinct_pharmacies", table=DRUGS, mode="value",
         question="Сколько разных аптек в данных о лекарствах?",
         gold='SELECT COUNT(DISTINCT "ID_Аптеки") FROM t'),

    # --- Задачи команд ---
    dict(id="tasks_by_status", table=TASKS, mode="all",
         question="Сколько задач команд в каждом статусе?",
         gold='SELECT "Статус", COUNT(*) FROM t GROUP BY 1'),
    dict(id="tasks_avg_actual_hours_by_type", table=TASKS, mode="all",
         question="Какие средние фактические трудозатраты в часах по типам задач?",
         gold='SELECT "Тип задачи", AVG("Фактические трудозатраты, ч") FROM t GROUP BY 1'),

    # --- Контрольные сценарии команды (числа подтверждены в PostgreSQL) ---
    dict(id="ctrl_all_rows", table=SALES, mode="value", expected=500000,
         question="Сколько всего записей в продажах?"),
    dict(id="ctrl_sale_ops", table=SALES, mode="value", expected=494181,
         question="Сколько операций «Продажа»?"),
    dict(id="ctrl_return_ops", table=SALES, mode="value", expected=5819,
         question="Сколько операций «Возврат» в продажах?"),
    dict(id="ctrl_rows_mobile", table=SALES, mode="value", expected=214505,
         question="Сколько строк продаж через мобильное приложение?"),
    dict(id="ctrl_rows_web", table=SALES, mode="value", expected=185463,
         question="Сколько строк продаж через интернет-магазин?"),
    dict(id="ctrl_rows_retail", table=SALES, mode="value", expected=100032,
         question="Сколько строк продаж через розницу?"),
    dict(id="ctrl_ambiguous_unit", table=SALES, mode="clarify", clarify="unit",
         question="Сколько продаж без карты лояльности?"),
    # В задаче команды было 122 805 — это только операции «Продажа»; всех строк без карты
    # 124 227 (проверено в PostgreSQL). Оба варианта — отдельными вопросами.
    dict(id="ctrl_rows_no_card", table=SALES, mode="value", expected=124227,
         question="Сколько строк продаж без карты?"),
    dict(id="ctrl_sale_ops_no_card", table=SALES, mode="value", expected=122805,
         question="Сколько операций «Продажа» без карты лояльности?"),
    dict(id="ctrl_orders_no_card", table=SALES, mode="value", expected=60138,
         question="Сколько уникальных заказов без карты?"),
    dict(id="ctrl_returns_short", table=SALES, mode="clarify", clarify="table",
         question="Сколько возвратов?"),  # возвраты есть в продажах, доставке, поддержке

    # --- Неподходящие вопросы ---
    dict(id="offtopic_weather", table=None, mode="refuse",
         question="Какая завтра погода в Москве?"),
    dict(id="refuse_delete", table=None, mode="refuse",
         question="Удали все записи о продажах"),

    # --- Контекст диалога ---
    dict(id="dialog_filter_month", table=SALES, mode="all",
         steps=["Какая выручка с учётом скидки по каналам продаж?",
                "А теперь только за сентябрь 2025 года"],
         question="А теперь только за сентябрь 2025 года",
         gold=f'SELECT "Канал продажи", {REVENUE} FROM t '
              'WHERE "Дата операции (МСК)" >= \'2025-09-01\' '
              'AND "Дата операции (МСК)" < \'2025-10-01\' GROUP BY 1'),
    dict(id="dialog_unit_answer", table=SALES, mode="value", expected=60138,
         steps=["Сколько продаж без карты лояльности?", "Количество уникальных: Код заказа"],
         question="Количество уникальных: Код заказа"),

    # --- Сравнение периодов и сегментов (#29) ---
    dict(id="compare_months_by_channel", table=SALES, mode="all",
         question="Сравни выручку с учётом скидки по каналам продаж за август и сентябрь 2025",
         gold=f'SELECT "Канал продажи", '
              f'{REVENUE} FILTER (WHERE "Дата операции (МСК)" >= \'2025-08-01\' '
              f'AND "Дата операции (МСК)" < \'2025-09-01\'), '
              f'{REVENUE} FILTER (WHERE "Дата операции (МСК)" >= \'2025-09-01\' '
              f'AND "Дата операции (МСК)" < \'2025-10-01\') FROM t GROUP BY 1'),
    dict(id="compare_years_qty", table=SALES, mode="all",
         question="Как изменилось количество проданных штук в 2025 году по сравнению с 2024?",
         gold='SELECT SUM("Количество, шт.") FILTER (WHERE "Дата операции (МСК)" >= \'2024-01-01\' '
              'AND "Дата операции (МСК)" < \'2025-01-01\'), '
              'SUM("Количество, шт.") FILTER (WHERE "Дата операции (МСК)" >= \'2025-01-01\' '
              'AND "Дата операции (МСК)" < \'2026-01-01\') FROM t'),
    dict(id="dialog_compare_prev_month", table=SALES, mode="all",
         steps=["Сколько штук товаров продано по каждому каналу продажи за сентябрь 2025?",
                "Сравни с предыдущим месяцем"],
         question="Сравни с предыдущим месяцем",
         gold='SELECT "Канал продажи", '
              'SUM("Количество, шт.") FILTER (WHERE "Дата операции (МСК)" >= \'2025-08-01\' '
              'AND "Дата операции (МСК)" < \'2025-09-01\'), '
              'SUM("Количество, шт.") FILTER (WHERE "Дата операции (МСК)" >= \'2025-09-01\' '
              'AND "Дата операции (МСК)" < \'2025-10-01\') FROM t GROUP BY 1'),
    dict(id="compare_two_platforms", table=ADS, mode="all",
         question="Сравни выручку по рекламе Яндекс Директ и VK Реклама",
         gold='SELECT "Площадка", SUM("Выручка по рекламе, руб.") FROM t '
              'WHERE "Площадка" IN (\'Яндекс Директ\', \'VK Реклама\') GROUP BY 1'),

    # --- What-if (#56): база и сценарий посчитаны в PostgreSQL ---
    dict(id="whatif_discount_pp", table=SALES, mode="all",
         question="Что будет с выручкой, если скидка вырастет на 5 п.п.?",
         gold=f'SELECT {REVENUE}, SUM("Количество, шт." * "Цена до скидки за единицу, руб." '
              f'* (1 - LEAST(100, GREATEST(0, "Скидка, %" + 5)) / 100.0)) FROM t'),
    dict(id="whatif_price_by_channel", table=SALES, mode="all",
         question="Что будет с выручкой по каналам продаж, если цены вырастут на 10%?",
         gold=f'SELECT "Канал продажи", {REVENUE}, SUM("Количество, шт." * '
              f'("Цена до скидки за единицу, руб." * 1.1) * (1 - "Скидка, %" / 100.0)) FROM t GROUP BY 1'),

    dict(id="dialog_offtopic_after_question", table=None, mode="refuse",
         steps=["Какая выручка с учётом скидки по каналам продаж?", "Какая погода в Москве?"],
         question="Какая погода в Москве?"),
]
