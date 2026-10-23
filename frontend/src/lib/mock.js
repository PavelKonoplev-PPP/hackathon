// Ответы-заглушки, чтобы разрабатывать интерфейс без бэкенда.
// Формат ответа — как в docs/api-contract.md.

const delay = (ms) => new Promise((r) => setTimeout(r, ms))

export async function mockReply(message) {
  await delay(700)
  const q = message.toLowerCase()

  if (q.includes('опиши данные') || (q.includes('что') && (q.includes('данн') || q.includes('есть')))) {
    return {
      answer:
        'Это данные интернет-магазина: продажи и возвраты товаров за период с октября 2024 года. ' +
        'В таблице 500 000 операций и 22 поля: заказ, покупатель, город, канал продажи, товар, цена, скидка, способ оплаты. ' +
        'Каждая строка — одна товарная позиция заказа.',
      table: {
        columns: ['Показатель', 'Значение'],
        rows: [
          ['Строк', '500 000'],
          ['Полей', '22'],
          ['Начало периода', '2024-10-01'],
          ['Каналов продажи', '3'],
        ],
      },
      sql: 'SELECT count(*) FROM sales;',
    }
  }

  if (q.includes('динамик') || q.includes('месяц') || q.includes('время')) {
    return {
      answer: 'Продажи по месяцам: после сентябрьского спада выручка растёт и достигает максимума в декабре.',
      chart: {
        type: 'line',
        title: 'Выручка по месяцам, млн руб.',
        categories: ['Окт', 'Ноя', 'Дек', 'Янв', 'Фев', 'Мар'],
        series: [{ name: 'Выручка', data: [41, 47, 58, 39, 36, 44] }],
      },
      sql: "SELECT date_trunc('month', op_date) AS m, sum(revenue) FROM sales GROUP BY 1 ORDER BY 1;",
    }
  }

  if (q.includes('регион') || q.includes('город') || q.includes('канал') || q.includes('продаж')) {
    return {
      answer: 'Больше всего продаж приходится на интернет-магазин, мобильное приложение заметно отстаёт.',
      chart: {
        type: 'bar',
        title: 'Продажи по каналам, тыс. операций',
        categories: ['Интернет-магазин', 'Розничный магазин', 'Мобильное приложение'],
        series: [{ name: 'Операций', data: [214, 176, 110] }],
      },
      sql: 'SELECT channel, count(*) FROM sales GROUP BY channel;',
    }
  }

  if (q.includes('дашборд')) {
    return {
      answer: 'Дашборды появятся на следующем этапе. Пока можно попросить построить отдельный график.',
    }
  }

  return {
    answer: 'Уточните, пожалуйста, что именно вы хотите узнать.',
    clarification: {
      question: 'Вот что я могу показать:',
      options: ['Опиши данные', 'Продажи по каналам', 'Динамика продаж по месяцам'],
    },
  }
}
