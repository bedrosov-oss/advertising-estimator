"""Deterministic RUB estimate and rectangular layout calculations, stdlib only."""

from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP, localcontext
import re


MAX_ROWS = 500
ZERO = Decimal("0")
HUNDRED = Decimal("100")
CENT = Decimal("0.01")
NUMBER = re.compile(r"^-?\d+(?:[.,]\d{1,6})?$")
CATEGORIES = {"material", "work", "service", "delivery"}
SOURCE_STATUSES = {
    "manual", "published_snapshot", "historical_snapshot", "conflict",
    "scope_needs_confirmation", "from_price",
}
ROW_FIELDS = (
    "id", "name", "category", "unit", "quantity", "price", "increment",
    "minimum_charge", "source", "price_date", "confirmed", "note", "source_status",
)


def _object(value, label):
    if not isinstance(value, dict):
        raise ValueError(f"{label}: ожидается объект.")
    return value


def _text(value, label, limit=2000):
    if not isinstance(value, str):
        raise ValueError(f"{label}: ожидается текст.")
    if len(value) > limit or any((ord(c) < 32 and c not in "\n\r\t") or 0xD800 <= ord(c) <= 0xDFFF for c in value):
        raise ValueError(f"{label}: текст слишком длинный или содержит недопустимые символы.")
    return value.strip()


def _boolean(value, label):
    if not isinstance(value, bool):
        raise ValueError(f"{label}: ожидается true или false.")
    return value


def _plain(value):
    text = format(value, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def _number(value, label, *, optional=False, positive=False, maximum="1000000000000"):
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise ValueError(f"{label}: ожидается десятичное число.")
    raw = str(value).strip()
    if raw == "" and optional:
        return None
    if len(raw) > 40 or not NUMBER.fullmatch(raw):
        raise ValueError(f"{label}: введите число без формул, не более 6 знаков после запятой.")
    try:
        parsed = Decimal(raw.replace(",", "."))
    except InvalidOperation as exc:
        raise ValueError(f"{label}: неверное число.") from exc
    if not parsed.is_finite() or parsed < ZERO or (positive and parsed <= ZERO):
        relation = "больше нуля" if positive else "неотрицательным"
        raise ValueError(f"{label}: число должно быть {relation}.")
    if parsed > Decimal(maximum):
        raise ValueError(f"{label}: превышено допустимое значение {maximum}.")
    return parsed


def _numeric_text(value, label, **kwargs):
    parsed = _number(value, label, **kwargs)
    return "" if parsed is None else _plain(parsed)


def _choice(value, label, choices):
    if not isinstance(value, str) or value not in choices:
        raise ValueError(f"{label}: неизвестное значение.")
    return value


def _money(value):
    return format(value.quantize(CENT, rounding=ROUND_HALF_UP), ".2f")


def _rounded(value):
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def validate_project(payload):
    """Return a new normalized object; never mutate caller input or trust flags."""
    payload = _object(payload, "Смета")
    version = payload.get("schema_version", 1)
    if type(version) is not int or version != 1:
        raise ValueError("Неподдерживаемая версия файла сметы.")
    project = _object(payload.get("project", {}), "Заказ")
    normalized_project = {
        key: _text(project.get(key, ""), label, limit)
        for key, label, limit in (
            ("title", "Название заказа", 500), ("client", "Заказчик", 500),
            ("city", "Город", 300), ("notes", "Примечания заказа", 10000),
        )
    }
    normalized_project["quantity"] = _numeric_text(
        project.get("quantity", ""), "Количество изделий", optional=True,
        positive=True, maximum="1000000000",
    )
    incoming_rows = payload.get("rows", [])
    if not isinstance(incoming_rows, list) or len(incoming_rows) > MAX_ROWS:
        raise ValueError(f"Смета должна содержать список не более {MAX_ROWS} строк.")
    normalized_rows = []
    ids = set()
    for index, incoming in enumerate(incoming_rows, 1):
        incoming = _object(incoming, f"Строка {index}")
        label = f"Строка {index}"
        row = {
            "id": _text(incoming.get("id", f"row-{index}"), f"{label}: идентификатор", 100),
            "name": _text(incoming.get("name", ""), f"{label}: название", 1000),
            "category": _choice(incoming.get("category", "material"), f"{label}: категория", CATEGORIES),
            "unit": _text(incoming.get("unit", ""), f"{label}: единица", 100),
            "quantity": _numeric_text(incoming.get("quantity", ""), f"{label}: количество", optional=True, positive=True, maximum="1000000000"),
            "price": _numeric_text(incoming.get("price", ""), f"{label}: цена", optional=True),
            "increment": _numeric_text(incoming.get("increment", ""), f"{label}: кратность", optional=True, positive=True, maximum="1000000000"),
            "minimum_charge": _numeric_text(incoming.get("minimum_charge", "0"), f"{label}: минимальная оплата"),
            "source": _text(incoming.get("source", ""), f"{label}: источник", 3000),
            "price_date": _text(incoming.get("price_date", ""), f"{label}: дата цены", 10),
            "confirmed": _boolean(incoming.get("confirmed", False), f"{label}: подтверждение"),
            "note": _text(incoming.get("note", ""), f"{label}: примечание", 10000),
            "source_status": _choice(incoming.get("source_status", "manual"), f"{label}: статус источника", SOURCE_STATUSES),
        }
        if not row["id"] or row["id"] in ids:
            raise ValueError(f"{label}: идентификатор пустой или повторяется.")
        ids.add(row["id"])
        if row["price_date"]:
            try:
                if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", row["price_date"]):
                    raise ValueError
                date.fromisoformat(row["price_date"])
            except ValueError as exc:
                raise ValueError(f"{label}: дата цены должна быть действительной датой ГГГГ-ММ-ДД.") from exc
        normalized_rows.append(row)
    settings = _object(payload.get("settings", {}), "Настройки")
    normalized_settings = {}
    for key, label, default, maximum in (
        ("overhead_percent", "Накладные расходы", "0", "1000"),
        ("reserve_percent", "Резерв", "0", "1000"),
        ("profit_percent", "Наценка или маржа", "30", "1000"),
        ("discount_percent", "Скидка", "0", "100"),
        ("tax_percent", "Ставка начисляемого налога", "", "100"),
    ):
        normalized_settings[key] = _numeric_text(
            settings.get(key, default), label, optional=(key == "tax_percent"), maximum=maximum,
        )
    normalized_settings["pricing_mode"] = _choice(settings.get("pricing_mode", "markup"), "Способ ценообразования", {"markup", "margin"})
    normalized_settings["tax_mode"] = _choice(settings.get("tax_mode", "unknown"), "Режим начисляемого налога", {"unknown", "none", "add"})
    for key in ("cost_basis_confirmed", "scope_confirmed"):
        normalized_settings[key] = _boolean(settings.get(key, False), key)
    if normalized_settings["pricing_mode"] == "margin" and Decimal(normalized_settings["profit_percent"]) >= HUNDRED:
        raise ValueError("Целевая маржа должна быть меньше 100%.")
    return {"schema_version": 1, "project": normalized_project, "rows": normalized_rows, "settings": normalized_settings}


def calculate(payload):
    """Calculate rounded line totals and explicitly track evidence and gaps."""
    project = validate_project(payload)
    with localcontext() as context:
        context.prec = 60
        return _calculate_normalized(project)


def _calculate_normalized(payload):
    settings = payload["settings"]
    warnings, missing, result_rows = [], [], []
    direct = ZERO
    amounts_known = bool(payload["rows"])
    today = date.today().isoformat()
    if not payload["rows"]:
        missing.append("Добавьте позиции сметы.")
    if not payload["project"]["title"]:
        missing.append("Название заказа.")
    if not payload["project"]["quantity"]:
        missing.append("Количество готовых изделий.")
    if not payload["project"]["notes"]:
        missing.append("Описание комплектации и условий заказа для заказчика.")
    for index, row in enumerate(payload["rows"], 1):
        row_warnings = []
        label = f"Строка {index} ({row['name'] or 'без названия'})"
        for key, description in (("name", "название"), ("unit", "единица измерения"), ("quantity", "количество"), ("price", "цена"), ("source", "источник цены"), ("price_date", "дата цены")):
            if not row[key]:
                missing.append(f"{label}: {description}.")
        if not row["confirmed"]:
            missing.append(f"{label}: подтвердите применимость цены и условий.")
        if row["price_date"] and row["price_date"] > today:
            missing.append(f"{label}: дата цены находится в будущем.")
            row_warnings.append("Дата цены находится в будущем и требует исправления.")
        if row["source_status"] in {"historical_snapshot", "conflict"}:
            reason = "историческая цена" if row["source_status"] == "historical_snapshot" else "противоречивые сведения о цене"
            missing.append(f"{label}: {reason}; требуется новый проверенный источник и статус manual.")
            row_warnings.append(f"Используется {reason}. Итог остаётся предварительным даже при отметке подтверждения.")
        elif row["source_status"] == "from_price":
            row_warnings.append("Исходная цена указана «от»; для заказа подтвердите точную расценку и условия.")
        elif row["source_status"] == "scope_needs_confirmation":
            row_warnings.append("Для исходной расценки требуется проверка состава, тиража и условий применения.")
        elif row["source_status"] == "published_snapshot" and not row["confirmed"]:
            row_warnings.append("Публичная цена является датированным наблюдением и ещё не подтверждена для заказа.")
        billed = None
        amount = None
        if row["quantity"]:
            billed = Decimal(row["quantity"])
            if row["increment"]:
                increment = Decimal(row["increment"])
                billed = (billed / increment).to_integral_value(rounding=ROUND_CEILING) * increment
                if billed != Decimal(row["quantity"]):
                    row_warnings.append("Количество округлено вверх до закупочной кратности.")
        if billed is not None and row["price"] != "":
            raw_amount = billed * Decimal(row["price"])
            minimum = Decimal(row["minimum_charge"])
            if minimum > raw_amount:
                row_warnings.append("Применена минимальная оплата строки; общий минимум счёта учитывайте один раз.")
            amount = _rounded(max(raw_amount, minimum))
            direct += amount
        else:
            amounts_known = False
            row_warnings.append("Сумма не определена: заполните количество и цену; пустое значение не считается нулём.")
        result_rows.append({**row, "billed_quantity": None if billed is None else _plain(billed), "amount": None if amount is None else _money(amount), "warnings": row_warnings})
    if not settings["cost_basis_confirmed"]:
        missing.append("Подтвердите приведение закупочных цен к единой базе с учётом входных налогов и скидок.")
    if not settings["scope_confirmed"]:
        missing.append("Подтвердите полноту состава работ, доставки, монтажа и отсутствие двойного учёта.")
    if settings["tax_mode"] == "unknown":
        missing.append("Режим начисляемого покупателю налога.")
    elif settings["tax_mode"] == "add" and settings["tax_percent"] == "":
        missing.append("Ставка начисляемого покупателю налога.")
    totals = {key: None for key in ("direct", "overhead", "reserve", "cost", "sale_before_discount", "discount", "sale_net", "tax", "total", "profit", "margin_percent", "unit_total")}
    totals["direct"] = _money(direct)
    if amounts_known:
        overhead = _rounded(direct * Decimal(settings["overhead_percent"]) / HUNDRED)
        reserve = _rounded((direct + overhead) * Decimal(settings["reserve_percent"]) / HUNDRED)
        cost = direct + overhead + reserve
        percentage = Decimal(settings["profit_percent"]) / HUNDRED
        sale = _rounded(cost * (1 + percentage) if settings["pricing_mode"] == "markup" else cost / (1 - percentage))
        discount = _rounded(sale * Decimal(settings["discount_percent"]) / HUNDRED)
        sale_net = sale - discount
        profit = sale_net - cost
        for key, value in (("overhead", overhead), ("reserve", reserve), ("cost", cost), ("sale_before_discount", sale), ("discount", discount), ("sale_net", sale_net), ("profit", profit)):
            totals[key] = _money(value)
        totals["margin_percent"] = _money(profit / sale_net * HUNDRED) if sale_net > ZERO else None
        if profit < ZERO:
            warnings.append("После скидки цена продажи ниже расчётной себестоимости.")
        if settings["tax_mode"] == "none":
            tax = ZERO
        elif settings["tax_mode"] == "add" and settings["tax_percent"] != "":
            tax = _rounded(sale_net * Decimal(settings["tax_percent"]) / HUNDRED)
        else:
            tax = None
        if tax is not None:
            total = sale_net + tax
            totals["tax"] = _money(tax)
            totals["total"] = _money(total)
            if payload["project"]["quantity"]:
                totals["unit_total"] = _money(total / Decimal(payload["project"]["quantity"]))
    else:
        warnings.append("Прямые затраты показывают только сумму заполненных строк. Общая цена заказа не определена.")
    complete = not missing
    if not complete:
        warnings.append("Смета предварительная. Числовые суммы при наличии всех расценок не подтверждают полноту и применимость исходных данных.")
    return {"rows": result_rows, "totals": totals, "complete": complete, "status": "Смета по подтверждённым данным" if complete else "Предварительная смета", "warnings": warnings, "missing": missing}


def geometry(payload):
    """Compare uniform rectangular grids; no mixed-layout optimization claim."""
    payload = _object(payload, "Раскрой")
    numbers = {}
    for key, label in (("width_mm", "Ширина детали"), ("height_mm", "Высота детали")):
        numbers[key] = _number(payload.get(key, ""), label, positive=True, maximum="10000000")
    numbers["quantity"] = _number(payload.get("quantity", ""), "Количество деталей", positive=True, maximum="1000000000")
    if numbers["quantity"] != numbers["quantity"].to_integral_value():
        raise ValueError("Количество деталей для раскроя должно быть целым.")
    for key, label, maximum in (("edge_mm", "Поле листа", "10000000"), ("gap_mm", "Междетальный зазор", "10000000"), ("bleed_mm", "Припуск детали", "10000000"), ("waste_percent", "Запас площади", "1000")):
        numbers[key] = _number(payload.get(key, "0"), label, maximum=maximum)
    for key, label in (("sheet_width_mm", "Ширина листа"), ("sheet_height_mm", "Высота листа")):
        numbers[key] = _number(payload.get(key, ""), label, optional=True, positive=True, maximum="10000000")
    allow_rotate = _boolean(payload.get("allow_rotate", True), "Разрешение поворота")
    if (numbers["sheet_width_mm"] is None) != (numbers["sheet_height_mm"] is None):
        raise ValueError("Для раскроя укажите обе стороны листа или оставьте обе пустыми.")
    with localcontext() as context:
        context.prec = 60
        width, height, qty = numbers["width_mm"], numbers["height_mm"], numbers["quantity"]
        bleed, gap, edge = numbers["bleed_mm"], numbers["gap_mm"], numbers["edge_mm"]
        part_w, part_h = width + 2 * bleed, height + 2 * bleed
        result = {
            "net_area_m2": _plain(width * height * qty / Decimal("1000000")),
            "billed_area_m2": _plain(part_w * part_h * qty * (1 + numbers["waste_percent"] / HUNDRED) / Decimal("1000000")),
            "perimeter_m": _plain(2 * (width + height) * qty / Decimal("1000")),
            "pieces_per_sheet": None, "sheets": None, "layout_label": "Площадь и периметр без раскроя листа",
            "notes": [
                "Оплачиваемая площадь предназначена для услуг с тарифом за м² и включает припуски и заданный запас.",
                "Периметр рассчитан по готовому размеру. Технологические траектории фрезы и общие резы здесь не определяются.",
            ],
        }
        if numbers["sheet_width_mm"] is not None:
            available_w = numbers["sheet_width_mm"] - 2 * edge
            available_h = numbers["sheet_height_mm"] - 2 * edge
            if available_w <= ZERO or available_h <= ZERO:
                raise ValueError("Поля полностью занимают лист; уменьшите поля или увеличьте лист.")
            layouts = []
            for rotated in (False, True) if allow_rotate else (False,):
                pw, ph = (part_h, part_w) if rotated else (part_w, part_h)
                columns = int(((available_w + gap) / (pw + gap)).to_integral_value(rounding=ROUND_FLOOR))
                rows = int(((available_h + gap) / (ph + gap)).to_integral_value(rounding=ROUND_FLOOR))
                layouts.append((columns * rows, columns, rows, rotated))
            chosen = max(layouts, key=lambda item: item[0])
            per_sheet, columns, rows, rotated = chosen
            if per_sheet == 0:
                raise ValueError("Деталь с припусками не помещается на лист с указанными полями.")
            result["pieces_per_sheet"] = per_sheet
            result["sheets"] = int((qty / Decimal(per_sheet)).to_integral_value(rounding=ROUND_CEILING))
            result["layout_label"] = f"{columns} × {rows}; {'поворот 90°' if rotated else 'без поворота'}; единая ориентация деталей"
            result["notes"].extend([
                "Сравниваются прямоугольные сетки с одинаковой ориентацией деталей; смешанный оптимальный раскрой не рассчитывается.",
                "Число листов учитывает поля, припуски и зазоры. Процент запаса площади повторно к листам не применяется; дополнительные листы для брака задаются отдельно.",
                "Разрешение поворота должно соответствовать направлению волокон, рисунка и ограничениям материала.",
            ])
        for key, label, unit in (
            ("net_area_m2", "Чистая площадь", "м²"),
            ("billed_area_m2", "Оплачиваемая площадь", "м²"),
            ("perimeter_m", "Периметр", "м"),
        ):
            exact = Decimal(result[key])
            if exact.as_tuple().exponent < -6:
                result[key] = _plain(exact.quantize(Decimal("0.000001"), rounding=ROUND_CEILING))
                result["notes"].append(f"{label} округлена вверх до 0,000001 {unit} для переноса в строку сметы." if key != "perimeter_m" else f"{label} округлён вверх до 0,000001 {unit} для переноса в строку сметы.")
        return result
