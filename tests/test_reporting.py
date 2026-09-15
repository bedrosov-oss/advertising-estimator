import unittest

from engine import validate_project
from reporting import export_csv, export_html, import_csv


def project():
    return {
        "project": {"title": "Печать табличек", "quantity": "20", "notes": "Комплектация согласуется"},
        "rows": [{"id": "one", "name": "СЕКРЕТ-ЗАКУПКА", "unit": "м²", "quantity": "2,5", "price": "100", "source": "СЕКРЕТ-ПОСТАВЩИК", "price_date": "2026-01-01", "note": "СЕКРЕТ-КОММЕНТАРИЙ", "confirmed": True}],
        "settings": {"tax_mode": "none", "cost_basis_confirmed": True, "scope_confirmed": True, "profit_percent": "30"},
    }


class ReportingTests(unittest.TestCase):
    def test_exchange_csv_round_trip_and_comma_decimal(self):
        payload = project()
        text = export_csv(payload)
        self.assertIn(";2,5;100;", text)
        self.assertEqual(import_csv("\ufeff" + text), validate_project(payload)["rows"])

    def test_import_common_comma_delimiter_and_unknown_price(self):
        rows = import_csv('name,unit,quantity,price\n"Бумага, белая",лист,20,\n')
        self.assertEqual(rows[0]["name"], "Бумага, белая")
        self.assertEqual(rows[0]["price"], "")
        self.assertFalse(rows[0]["confirmed"])

    def test_formula_cells_are_escaped_even_with_leading_whitespace(self):
        payload = project()
        payload["rows"][0].update(name='=HYPERLINK("https://evil.invalid")', source="@SUM(1;2)", note="  +SUM(1;2)")
        text = export_csv(payload)
        self.assertIn("'=HYPERLINK", text)
        self.assertIn("'@SUM", text)
        self.assertIn("'+SUM", text)
        self.assertEqual(import_csv(text), validate_project(payload)["rows"])

    def test_formula_as_price_rejected_and_never_evaluated(self):
        with self.assertRaises(ValueError):
            import_csv('name;unit;quantity;price\nПВХ;лист;1;=2+2\n')

    def test_csv_retains_literal_leading_apostrophes(self):
        payload = project()
        payload["rows"][0].update(name="'=literal", source="'quoted source", note="''two apostrophes")
        self.assertEqual(import_csv(export_csv(payload)), validate_project(payload)["rows"])

    def test_csv_rejects_oversized_missing_duplicate_extra_headers_and_rows(self):
        values = [
            "\ud800",
            "x" * (2 * 1024 * 1024 + 1), "name;price\nПВХ;10\n",
            "name;unit;quantity;price;name\nПВХ;лист;1;2;повтор\n",
            "name;unit;quantity;price;garbage\nПВХ;лист;1;2;x\n",
            "name;unit;quantity;price\nПВХ;лист;1;2;extra\n",
            "name;unit;quantity;price\n" + "ПВХ;лист;1;2\n" * 501,
        ]
        for value in values:
            with self.subTest(value=value[:80]), self.assertRaises(ValueError):
                import_csv(value)

    def test_html_escapes_all_external_text_and_has_no_scripts(self):
        payload = project()
        payload["project"]["title"] = '<script>alert("title")</script>'
        payload["project"]["notes"] = '<img src=x onerror="alert(1)">'
        payload["rows"][0]["source"] = '<iframe src="https://evil.invalid">'
        payload["rows"][0]["name"] = "<b>line</b>"
        for client_view in (True, False):
            text = export_html(payload, client_view)
            self.assertNotIn("<script", text)
            self.assertNotIn("<img", text)
            self.assertNotIn("<iframe", text)
            self.assertIn("&lt;script&gt;", text)

    def test_client_report_omits_internal_costs_notes_sources_and_profit(self):
        text = export_html(project(), True)
        for internal in ("СЕКРЕТ-ЗАКУПКА", "СЕКРЕТ-ПОСТАВЩИК", "СЕКРЕТ-КОММЕНТАРИЙ", "себестоимость", "маржа", "наценка", "250,00"):
            self.assertNotIn(internal, text)
        self.assertIn("325,00", text)
        self.assertIn("Печать табличек", text)
        self.assertIn("Комплектация согласуется", text)
        self.assertIn("16,25", text)

    def test_internal_report_shows_cost_source_and_status(self):
        text = export_html(project())
        self.assertIn("СЕКРЕТ-ЗАКУПКА", text)
        self.assertIn("СЕКРЕТ-ПОСТАВЩИК", text)
        self.assertIn("250,00", text)
        self.assertIn("325,00", text)
        self.assertIn("manual", text)

    def test_partial_report_does_not_convert_partial_subtotal_into_customer_price(self):
        payload = project()
        payload["rows"].append({"id": "two", "name": "Доставка", "unit": "рейс", "quantity": "1", "price": ""})
        text = export_html(payload, True)
        self.assertIn("Требует уточнения", text)
        self.assertIn("не определена", text)
        self.assertNotIn("325,00", text)
        self.assertNotIn("250,00", text)

    def test_unknown_tax_report_does_not_make_a_tax_inclusive_total(self):
        payload = project()
        payload["settings"]["tax_mode"] = "unknown"
        text = export_html(payload, True)
        self.assertIn("325,00", text)  # Known sale before tax is disclosed.
        self.assertIn("Общий итог не определён", text)
        self.assertIn("Предварительная смета", text)

    def test_rounded_unit_average_does_not_replace_authoritative_order_total(self):
        payload = project()
        payload["project"]["quantity"] = "3"
        payload["rows"][0].update(quantity="1", price="100")
        payload["settings"]["profit_percent"] = "0"
        for client_view in (False, True):
            text = export_html(payload, client_view)
            self.assertIn("33,33", text)
            self.assertIn("100,00", text)
            self.assertNotIn("99,99", text)
            self.assertIn("Средняя цена", text)
            self.assertIn("округлена до копеек", text)
            self.assertIn("итог за весь заказ", text)

    def test_client_report_identifies_missing_composition_without_procurement_details(self):
        payload = project()
        payload["project"]["notes"] = ""
        text = export_html(payload, True)
        self.assertIn("Комплектация заказа не описана", text)
        self.assertIn("Предварительная смета", text)
        self.assertNotIn("СЕКРЕТ-ЗАКУПКА", text)


if __name__ == "__main__":
    unittest.main()
