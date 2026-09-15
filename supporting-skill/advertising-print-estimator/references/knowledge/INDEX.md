# Документы и база сметчика, версия 3.1

Это автономное дополнение к комплекту. Инструкции читаются по задаче; весь справочник не требуется загружать в контекст одновременно. Локальный поиск работает без интернета и не обращается к памяти аккаунта. Онлайн-проверка новых цен требует доступного поиска.

## Что открыть

| Задача | Файл |
| --- | --- |
| Подготовить дополнения для своего производства | [WHAT_TO_ADD.md](../../../WHAT_TO_ADD.md) |
| Установить обязательные параметры материала | [data/materials.json](data/materials.json) |
| Разложить заказ на операции | [data/operations.json](data/operations.json) |
| Найти опубликованную цену | [Читаемая таблица](docs/price-overview.md) и [data/prices.json](data/prices.json) |
| Применить минимум заказа, состав услуги, разрешить конфликт | [data/conditions.json](data/conditions.json) |
| Найти один из семи добавленных сайтов | [Покрытие и правила](docs/connected-websites.md), [data/websites.json](data/websites.json) |
| Применить новые цены на примерах | [examples/new-sources.md](examples/new-sources.md) |
| Проверить источник и дату | [data/sources.json](data/sources.json) |
| Рассчитать расход, раскрой и цену | [docs/calculation-method.md](docs/calculation-method.md) |
| Проверить макет | [docs/prepress.md](docs/prepress.md) |
| Найти документацию производителя | [docs/technical-documents.md](docs/technical-documents.md) |
| Подготовить входящие документы | [docs/required-inputs.md](docs/required-inputs.md) |
| Проверить работу на примере | [examples/pvc-signs-20.md](examples/pvc-signs-20.md) |
| Уточнить структуру базы | [docs/database.md](docs/database.md) |

## Правила использования базы

Сначала найти материал/операцию, затем цену с подходящими характеристиками. Читать запись prices вместе со всеми condition_ids и source_id. Цена опубликованной страницы не является подтверждённым предложением на новый заказ. Статусы conflict, from_price, scope_needs_confirmation и historical_snapshot требуют отдельного разрешения; не выбирать такую цену автоматически для окончательной сметы. Даты доступа и даты изменения прайса хранятся отдельно.

Все суммы price_minor хранятся в копейках: 513000 означает 5130.00 RUB. null означает неизвестное, не ноль. База не присваивает поставщикам ставку НДС и не обещает наличие в регионе. Группы материалов и операций являются авторскими перечнями вопросов, а не ГОСТ, паспортом продукта или нормой выработки.

## Локальный поиск

Из папки advertising-print-estimator, при наличии Python 3:

```bash
python3 scripts/kb.py stats
python3 scripts/kb.py search "ПВХ" --section prices
python3 scripts/kb.py show Q01
python3 scripts/kb.py search "монтаж" --section operations
python3 scripts/kb.py demo
```

Поиск возвращает карточку вместе с относимыми условиями и источниками. Без Python можно читать JSON и Markdown напрямую. [estimator.sqlite](estimator.sqlite) — готовая локальная копия тех же данных; первичны файлы data/*.json. После осознанного редактирования данных выполнить `python3 scripts/kb.py validate`, затем `python3 scripts/kb.py rebuild`.

## Шаблоны

- [Техническое задание](templates/order-brief.md).
- [Внутренние расценки предприятия](templates/company-rates.json).
- [Карточка нового предложения](templates/quote-record.json).
- [Структура сметы](templates/estimate.json).
- [Данные для перевозки](templates/freight-request.json).

Заполнять шаблоны в папке конкретного заказа, не поверх исходных образцов. Прайсы клиентов и кадровые сведения в общий справочник не добавлять без поручения.
