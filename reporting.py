"""Escaped standalone HTML reports and a bounded, formula-safe CSV interchange."""

import csv
from datetime import date
from html import escape
from io import StringIO

from engine import MAX_ROWS, ROW_FIELDS, calculate, validate_project
from local_store import normalize_project
from fns_registry import customer_lines


MAX_CSV_BYTES = 2 * 1024 * 1024
NUMERIC_FIELDS = {"quantity", "price", "increment", "minimum_charge"}


def _safe_csv(value):
    value = str(value)
    # Spreadsheet apps may ignore leading spaces before recognizing a formula.
    if value.startswith(("'", "\t", "\r", "\n")) or value.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def export_csv(project):
    """Export cost rows for interchange, not a client-facing commercial offer."""
    normalized = validate_project(project)
    output = StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=ROW_FIELDS, delimiter=";", lineterminator="\r\n")
    writer.writeheader()
    for row in normalized["rows"]:
        converted = {}
        for key in ROW_FIELDS:
            value = row[key]
            if key in NUMERIC_FIELDS:
                value = value.replace(".", ",")
            elif key == "confirmed":
                value = "true" if value else "false"
            converted[key] = _safe_csv(value)
        writer.writerow(converted)
    return output.getvalue()


def import_csv(text):
    """Read row-schema CSV. Imported text never runs as a spreadsheet formula."""
    if not isinstance(text, str):
        raise ValueError("CSV должен быть текстом UTF-8 размером не более 2 МБ.")
    try:
        byte_length = len(text.encode("utf-8"))
    except UnicodeEncodeError as exc:
        raise ValueError("CSV содержит недопустимые символы Unicode.") from exc
    if byte_length > MAX_CSV_BYTES:
        raise ValueError("CSV должен быть текстом UTF-8 размером не более 2 МБ.")
    text = text.lstrip("\ufeff")
    if not text.strip():
        raise ValueError("CSV пуст.")
    first_line = text.splitlines()[0]
    delimiter = ";" if ";" in first_line else ","
    try:
        reader = csv.DictReader(StringIO(text, newline=""), delimiter=delimiter, strict=True)
        headers = reader.fieldnames
        if not headers or len(set(headers)) != len(headers):
            raise ValueError("CSV: отсутствуют заголовки или заголовки повторяются.")
        unknown = set(headers) - set(ROW_FIELDS)
        if unknown:
            raise ValueError("CSV: неизвестные столбцы. Используйте шаблон экспорта программы.")
        if not {"name", "unit", "quantity", "price"}.issubset(headers):
            raise ValueError("CSV должен содержать столбцы name, unit, quantity, price.")
        rows = []
        for index, raw in enumerate(reader, 1):
            if index > MAX_ROWS:
                raise ValueError(f"CSV содержит более {MAX_ROWS} строк.")
            if None in raw or any(value is None for value in raw.values()):
                raise ValueError(f"CSV, строка {index + 1}: число ячеек не соответствует заголовкам.")
            row = {}
            for key, value in raw.items():
                # Undo only our apostrophe escape, retaining all other apostrophes.
                if value.startswith("''") or (value.startswith("'") and (value[1:].lstrip().startswith(("=", "+", "-", "@")) or value[1:].startswith(("\t", "\r", "\n")))):
                    value = value[1:]
                row[key] = value
            if "confirmed" in row:
                flag = row["confirmed"].strip().lower()
                if flag not in {"", "true", "false", "1", "0", "да", "нет"}:
                    raise ValueError(f"CSV, строка {index + 1}: поле confirmed должно быть true или false.")
                row["confirmed"] = flag in {"true", "1", "да"}
            for key, default in (("id", f"row-{index}"), ("category", "material"), ("minimum_charge", "0"), ("source_status", "manual")):
                if not row.get(key, "").strip():
                    row[key] = default
            rows.append(row)
    except csv.Error as exc:
        raise ValueError("CSV повреждён: проверьте разделители и кавычки.") from exc
    return validate_project({"rows": rows})["rows"]


def _show_money(value):
    return "Требует уточнения" if value is None else f"{escape(value.replace('.', ','))} ₽"


def _paragraph(text):
    return escape(text).replace("\n", "<br>")


def export_html(project, client_view=False):
    """Produce a printable, self-contained report with no external requests."""
    if not isinstance(client_view, bool):
        raise ValueError("client_view должен быть true или false.")
    normalized = normalize_project(project)
    result = calculate(normalized)
    metadata, settings, totals = normalized["project"], normalized["settings"], result["totals"]
    title = metadata["title"] or "Заказ без названия"
    heading = "Смета для заказчика" if client_view else "Внутренняя калькуляция"
    sections = [
        "<!doctype html><html lang=\"ru\"><head><meta charset=\"utf-8\">",
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f"<title>{escape(heading)}: {escape(title)}</title>",
        '<style>body{font:15px/1.5 Arial,sans-serif;color:#192838;max-width:1120px;margin:36px auto;padding:0 20px}h1{font-size:26px;margin-bottom:4px}h2{font-size:18px;margin-top:28px}p{margin:8px 0}.status{display:inline-block;padding:5px 10px;background:#edf3f7;border-radius:5px}table{border-collapse:collapse;width:100%;margin:18px 0;font-size:13px}th,td{padding:9px;border:1px solid #cbd5df;text-align:left;vertical-align:top;overflow-wrap:anywhere}th{background:#edf3f7}.number{text-align:right;white-space:nowrap}.totals{max-width:660px;margin-left:auto}.total{font-size:17px;font-weight:bold}.muted{color:#566373}.warning{background:#fff5dc;padding:12px;border-left:4px solid #c3952d}ul{padding-left:22px}.notes{white-space:normal}footer{border-top:1px solid #cbd5df;margin-top:30px;padding-top:10px;color:#566373;font-size:12px}@media print{body{margin:0;padding:0;max-width:none}thead{display:table-header-group}tr{break-inside:avoid}h1,h2{break-after:avoid}a{color:inherit;text-decoration:none}@page{size:A4 landscape;margin:15mm}}</style></head><body>',
        f"<h1>{escape(heading)}</h1><p>{escape(title)}</p>",
        f'<p class="status">{escape(result["status"])}</p>',
        f'<p class="muted">Дата формирования: {date.today().isoformat()} · Валюта: RUB</p>',
        f'<p>Заказчик: {escape(metadata["client"] or "не указан")}<br>Город: {escape(metadata["city"] or "не указан")}<br>Количество готовых изделий: {escape(metadata["quantity"] or "требует уточнения")}</p>',
    ]
    sections.extend('<p>'+escape(line)+'</p>' for line in customer_lines(normalized.get('extensions',{}).get('customer',{})))
    if client_view:
        sections.append('<table><thead><tr><th>Изделие / заказ</th><th>Количество</th><th>Средняя цена единицы, округлённо</th><th>Сумма заказа</th></tr></thead><tbody>')
        sections.append(f'<tr><td>{escape(title)}</td><td>{escape(metadata["quantity"] or "Требует уточнения")}</td><td>{_show_money(totals["unit_total"])}</td><td>{_show_money(totals["total"])}</td></tr></tbody></table>')
        sections.append('<table class="totals"><tbody>')
        sections.append(f'<tr><td>Цена заказа до начисляемого налога</td><td class="number">{_show_money(totals["sale_net"])}</td></tr>')
        tax_label = "Налог не начисляется по указанным условиям" if settings["tax_mode"] == "none" else "Начисляемый налог"
        sections.append(f'<tr><td>{tax_label}</td><td class="number">{_show_money(totals["tax"])}</td></tr>')
        sections.append(f'<tr class="total"><td>Итого для заказчика</td><td class="number">{_show_money(totals["total"])}</td></tr></tbody></table>')
        client_gaps = []
        if not result["complete"]:
            client_gaps.append("Расчёт предварительный; цена и условия требуют подтверждения перед согласованием заказа.")
        if not normalized["rows"] or any(row["amount"] is None for row in result["rows"]):
            client_gaps.append("Стоимость части составляющих не определена. Полная цена заказа и цена единицы требуют уточнения.")
        if settings["tax_mode"] == "unknown" or (settings["tax_mode"] == "add" and settings["tax_percent"] == ""):
            client_gaps.append("Условия начисления налога покупателю требуют уточнения. Общий итог не определён.")
        if not settings["scope_confirmed"]:
            client_gaps.append("Состав заказа, доставка и монтаж требуют подтверждения.")
        if not metadata["notes"]:
            client_gaps.append("Комплектация заказа не описана. Уточните состав поставки и работ перед согласованием.")
        if any(row["source_status"] in {"historical_snapshot", "conflict"} for row in normalized["rows"]):
            client_gaps.append("Часть исходных сведений о стоимости требует актуализации или разрешения противоречий.")
        if client_gaps:
            sections.append('<h2>Условия и ограничения</h2><div class="warning"><ul>' + "".join(f"<li>{escape(text)}</li>" for text in client_gaps) + '</ul></div>')
    else:
        sections.append('<table><thead><tr><th>Позиция</th><th>Ед.</th><th>Потребность</th><th>К оплате</th><th>Цена</th><th>Сумма</th><th>Источник и условия</th></tr></thead><tbody>')
        for row in result["rows"]:
            source = f'{escape(row["source"] or "Источник не указан")}<br>Дата цены: {escape(row["price_date"] or "не указана")}<br>Статус: {escape(row["source_status"])}<br>Подтверждено: {"да" if row["confirmed"] else "нет"}<br>Кратность: {escape(row["increment"] or "без округления")}<br>Минимум строки: {_show_money(row["minimum_charge"])}'
            if row["note"]:
                source += "<br>" + _paragraph(row["note"])
            if row["warnings"]:
                source += "<br>" + "<br>".join(escape(warning) for warning in row["warnings"])
            sections.append(f'<tr><td>{escape(row["name"] or "Без названия")}</td><td>{escape(row["unit"])}</td><td>{escape(row["quantity"] or "?")}</td><td>{escape(row["billed_quantity"] or "?")}</td><td>{_show_money(row["price"] if row["price"] != "" else None)}</td><td>{_show_money(row["amount"])}</td><td>{source}</td></tr>')
        sections.append('</tbody></table><table class="totals"><tbody>')
        direct_label = "Прямые затраты" if normalized["rows"] and all(row["amount"] is not None for row in result["rows"]) else "Сумма заполненных строк (частичная)"
        mode_label = "Наценка" if settings["pricing_mode"] == "markup" else "Целевая маржа"
        labels = [
            ("direct", direct_label), ("overhead", f'Накладные расходы {settings["overhead_percent"]}% от прямых затрат'),
            ("reserve", f'Резерв {settings["reserve_percent"]}% от прямых и накладных затрат'),
            ("cost", "Расчётная себестоимость"), ("sale_before_discount", f'Цена до скидки; {mode_label.lower()} {settings["profit_percent"]}%'),
            ("discount", f'Скидка {settings["discount_percent"]}%'), ("sale_net", "Цена до начисляемого налога"),
            ("tax", "Начисляемый налог"), ("total", "Итого для заказчика"),
            ("unit_total", "Средняя цена одного готового изделия, округлённо"), ("profit", "Расчётная прибыль после скидки, до иных налогов"),
        ]
        for key, label in labels:
            row_class = ' class="total"' if key == "total" else ""
            sections.append(f'<tr{row_class}><td>{escape(label)}</td><td class="number">{_show_money(totals[key])}</td></tr>')
        margin_text = "Не определена при нулевой выручке или неполном расчёте" if totals["margin_percent"] is None else escape(totals["margin_percent"].replace(".", ",")) + "%"
        sections.append(f'<tr><td>Расчётная маржа после скидки</td><td class="number">{margin_text}</td></tr></tbody></table>')
        if result["warnings"] or result["missing"]:
            sections.append('<h2>Уточнения и основания предварительного статуса</h2><div class="warning"><ul>' + "".join(f"<li>{escape(text)}</li>" for text in result["warnings"] + result["missing"]) + '</ul></div>')
    if totals["unit_total"] is not None:
        sections.append('<p class="muted">Средняя цена единицы округлена до копеек. При умножении на количество возможна разница округления; расчётной суммой к оплате является итог за весь заказ.</p>')
    if metadata["notes"]:
        sections.append('<h2>Комплектация и условия заказа</h2><p class="notes">' + _paragraph(metadata["notes"]) + '</p>')
    sections.append('<footer>Сметчик рекламы и полиграфии · Локальный расчёт. Срок действия цены, сроки работ, оплата и гарантия определяются отдельно.</footer></body></html>')
    return "".join(sections)
