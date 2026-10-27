"""«Незнакомые» вопросы для проверки агента (BI-06 / #34).

Эти вопросы не использовались при настройке агента: другие формулировки, другие колонки
и таблицы, чем в eval_cases.py. По ним видно, как агент справится с вопросами, которых
команда заранее не видела. Правило: если кейс отсюда провалился, агент чинят в общем виде,
а сам кейс НЕ переписывают под агента. Новые незнакомые вопросы — добавлять сюда.

Формат тот же, что в eval_cases.py. Запуск:
    python -m backend.llm.eval --holdout --gold-only     # проверить эталоны
    python -m backend.llm.eval --holdout                 # агент напрямую
    python -m backend.llm.eval --holdout --api http://localhost:8000
"""
from .eval_cases import ADS, DELIVERY, EVENTS, JOBS, PURCHASE, REVENUE, SALES, SUPPORT, TASKS

HOLDOUT = [
    dict(id="ho_revenue_by_district", table=SALES, mode="all",
         question="Покажи выручку с учётом скидки в разрезе федеральных округов",
         gold=f'SELECT "Федеральный округ", {REVENUE} FROM t GROUP BY 1'),
    dict(id="ho_top_payment_method", table=SALES, mode="top",
         question="Какой способ оплаты используют чаще всего? Назови один, по числу записей о продажах",
         gold='SELECT "Способ оплаты", COUNT(*) FROM t GROUP BY 1 ORDER BY 2 DESC LIMIT 1'),
    dict(id="ho_qty_2025", table=SALES, mode="value",
         question="Сколько штук товаров продали за 2025 год?",
         gold='SELECT SUM("Количество, шт.") FROM t WHERE "Дата операции (МСК)" >= \'2025-01-01\' '
              'AND "Дата операции (МСК)" < \'2026-01-01\''),
    dict(id="ho_support_rating_by_group", table=SUPPORT, mode="all",
         question="Средняя оценка клиентов в каждой группе поддержки",
         gold='SELECT "Группа поддержки", AVG("Оценка клиента") FROM t GROUP BY 1'),
    dict(id="ho_delivery_attempts_by_carrier", table=DELIVERY, mode="all",
         question="Сколько в среднем попыток вручения приходится на отправление у каждого перевозчика?",
         gold='SELECT "Перевозчик", AVG("Попытки вручения") FROM t GROUP BY 1'),
    dict(id="ho_delivery_cost_by_status", table=DELIVERY, mode="all",
         question="Какая суммарная стоимость доставки по статусам отправлений?",
         gold='SELECT "Статус", SUM("Стоимость доставки, руб.") FROM t GROUP BY 1'),
    dict(id="ho_app_payments_total", table=EVENTS, mode="value",
         question="На какую общую сумму пользователи оплатили в приложении?",
         gold='SELECT SUM("Сумма оплаты, руб.") FROM t'),
    dict(id="ho_app_processing_by_platform", table=EVENTS, mode="all",
         question="Среднее время обработки событий в миллисекундах на каждой платформе",
         gold='SELECT "Платформа", AVG("Время обработки, мс") FROM t GROUP BY 1'),
    dict(id="ho_ads_orders_by_platform", table=ADS, mode="all",
         question="Сколько заказов принесла реклама на каждой площадке?",
         gold='SELECT "Площадка", SUM("Заказы по рекламе") FROM t GROUP BY 1'),
    dict(id="ho_purchase_approved_by_urgency", table=PURCHASE, mode="all",
         question="Какая средняя согласованная сумма заявок на закупку в зависимости от срочности?",
         gold='SELECT "Срочность", AVG("Согласованная сумма, руб.") FROM t GROUP BY 1'),
    dict(id="ho_jobs_by_environment", table=JOBS, mode="all",
         question="Сколько запусков фоновых заданий было в каждом окружении?",
         gold='SELECT "Окружение", COUNT(*) FROM t GROUP BY 1'),
    dict(id="ho_tasks_by_team", table=TASKS, mode="all",
         question="Сколько задач у каждой команды?",
         gold='SELECT "Команда", COUNT(*) FROM t GROUP BY 1'),
    dict(id="ho_dialog_ads_spend_2025", table=ADS, mode="all",
         steps=["Какие расходы на рекламу по каждой площадке?", "А только за 2025 год?"],
         question="А только за 2025 год?",
         gold='SELECT "Площадка", SUM("Расходы, руб.") FROM t WHERE "Дата" >= \'2025-01-01\' '
              'AND "Дата" < \'2026-01-01\' GROUP BY 1'),
    dict(id="ho_refuse_poem", table=None, mode="refuse",
         question="Напиши стихотворение про осень"),
    dict(id="ho_refuse_update", table=None, mode="refuse",
         question="Поменяй статус всех заявок на закупку на «Исполнена»"),
]
