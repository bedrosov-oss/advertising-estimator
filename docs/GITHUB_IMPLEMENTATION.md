# Как использованы найденные проекты GitHub

Выпуск 1.0.6 от 14 сентября 2026 года. Реализованы пять последовательных этапов; альтернативы с одинаковой функцией не устанавливались одновременно.

| Проект | Решение в программе |
|---|---|
| [pdfplumber](https://github.com/jsvine/pdfplumber) | Чтение таблиц и текста выбранной страницы PDF |
| [Camelot](https://github.com/camelot-dev/camelot) | Альтернатива; выбран pdfplumber, дополнительные ML-модули не включены |
| [Docling](https://github.com/docling-project/docling) | Альтернатива для сложных документов; модели и тяжёлый конвейер не включены |
| [PaddleOCR](https://github.com/PaddlePaddle/PaddleOCR) | Альтернатива; реализован отдельный OCR через Tesseract |
| [OCRmyPDF](https://github.com/ocrmypdf/OCRmyPDF) | Альтернатива для подготовки поискового слоя; оригиналы PDF не переписываются |
| [python-calamine](https://github.com/dimastbk/python-calamine) | Сохранён существующий ограниченный XLSX-парсер; XLS/XLSB/ODS в этот выпуск не добавлены |
| [XlsxWriter](https://github.com/jmcnamara/XlsxWriter) | Новая внутренняя калькуляция с редактируемыми вводами и формулами |
| [python-docx-template](https://github.com/elapouya/python-docx-template) | Клиентская смета Word, стандартный и пользовательский шаблон |
| [Vink Masterskaya](https://github.com/Vink-Masterskaya/vink-master) | Изучен как ориентир. Код без объявленной лицензии не копировался; адаптеры написаны отдельно |
| [Playwright Python](https://github.com/microsoft/playwright-python) | Подготовлен отдельный браузерный тест. Установка Chromium в среде разработки не завершилась |
| [Scrapy](https://github.com/scrapy/scrapy) | Не выбран: используются точечные запросы сохранённых источников, массового обхода сайтов нет |
| [extruct](https://github.com/scrapinghub/extruct) | Альтернатива расширения Microdata/RDFa; текущий общий адаптер проверяет JSON-LD Product/Offer |
| [changedetection.io](https://github.com/dgtlmoon/changedetection.io) | Отдельный сервис не разворачивается; журнал и плановые проверки реализованы внутри приложения |
| [APScheduler](https://github.com/agronholm/apscheduler) | Стабильная ветка 3.11.3 для расписания при открытом приложении |
| [RapidFuzz](https://github.com/rapidfuzz/RapidFuzz) | Подсказки похожих названий с фильтром единицы; автоматического объединения нет |
| [rectpack](https://github.com/secnot/rectpack) | Смешанная раскладка прямоугольников; независимая проверка количества, границ и пересечений |
| [OR-Tools](https://github.com/google/or-tools) | Альтернатива для будущей оптимизации партий и графика станков; в текущий расчёт не включён |
| [ezdxf](https://github.com/mozman/ezdxf) | Локальное чтение плоской DXF-геометрии, единиц и слоя |
| [svgpathtools](https://github.com/mathandy/svgpathtools) | Длина SVG-путей с преобразованиями и физическим масштабом |
| [Deepnest](https://github.com/deepnest-next/deepnest) | Внешний вариант фигурного раскроя; не встроен из-за отдельной цепочки конвертации и набора лицензий |
| [pywebview](https://github.com/r0x0r/pywebview) | Отдельное окно с резервным открытием в браузере |
| [PyInstaller](https://github.com/pyinstaller/pyinstaller) | Сценарий автономной сборки на целевой ОС, ресурсы и версии пакета |
| [Briefcase](https://github.com/beeware/briefcase) | Альтернатива упаковки; выбран PyInstaller |
| [uv](https://github.com/astral-sh/uv) | Разрешение зависимостей и lock-файл с хешами; пользовательский запуск сохраняет установщик Python и venv |
| [dadata-py](https://github.com/hflabs/dadata-py) | Существующий вызов API DaData сохранён; SDK не добавлен как дублирующая зависимость |
| [keyring](https://github.com/jaraco/keyring) | Явное сохранение ключей в системном хранилище; текстовый запасной файл запрещён |
| [ollama-python](https://github.com/ollama/ollama-python) | Сохранена существующая локальная интеграция через HTTP; SDK не обязателен |
| [tavily-python](https://github.com/tavily-ai/tavily-python) | Сохранена существующая интеграция REST с ключом пользователя; SDK не обязателен |
| [InvenTree](https://github.com/inventree/InvenTree) | Реализован небольшой локальный количественный склад. Развёртывание и синхронизация InvenTree не входят в выпуск |
| [Hypothesis](https://github.com/HypothesisWorks/hypothesis) | Генерируемые проверки сохранения денежных сумм, неизвестных значений и раскроя |
| [setup-python](https://github.com/actions/setup-python) | Workflow сборки Mac Apple Silicon, Mac Intel и Windows; внешний запуск GitHub ещё не выполнялся |

В комплекте нет скрыто развёрнутых серверов, скачанных моделей ИИ или действующих ключей. В исходном каталоге отдельно исключены демонстрационный `egrul-api-client` с синтетическими данными, `egrul-nalog-parser` без объявленной лицензии, пустой `freshquoteprint` и старый `printing-quotation`. Эти проекты не подключались.
