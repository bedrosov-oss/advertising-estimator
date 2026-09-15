import copy
import unittest

from engine import calculate, geometry, validate_project


def sample_project():
    return {
        "schema_version": 1,
        "project": {"title": "20 табличек", "client": "Заказчик", "city": "", "quantity": "20", "notes": "20 табличек ПВХ 600 × 400 мм. Печать, доставка и монтаж исключены."},
        "rows": [{
            "id": "pvc", "name": "ПВХ", "category": "material", "unit": "м²",
            "quantity": "4.8", "price": "855", "increment": "3", "minimum_charge": "0",
            "source": "Согласованный прайс, строка 1", "price_date": "2026-01-01",
            "confirmed": True, "note": "", "source_status": "manual",
        }],
        "settings": {
            "overhead_percent": "0", "reserve_percent": "0", "pricing_mode": "markup",
            "profit_percent": "30", "discount_percent": "0", "tax_mode": "none",
            "tax_percent": "", "cost_basis_confirmed": True, "scope_confirmed": True,
        },
    }


class CalculationTests(unittest.TestCase):
    def test_purchase_increment_and_customer_unit(self):
        result = calculate(sample_project())
        self.assertEqual(result["rows"][0]["billed_quantity"], "6")
        self.assertEqual(result["rows"][0]["amount"], "5130.00")
        self.assertEqual(result["totals"]["total"], "6669.00")
        self.assertEqual(result["totals"]["unit_total"], "333.45")
        self.assertTrue(result["complete"])

    def test_minimum_tariff_and_shared_minimum_warning(self):
        project = sample_project()
        project["rows"][0].update(quantity="40", price="21", increment="", minimum_charge="7500")
        result = calculate(project)
        self.assertEqual(result["rows"][0]["amount"], "7500.00")
        self.assertTrue(any("один раз" in warning for warning in result["rows"][0]["warnings"]))

    def test_round_each_row_then_sum_half_up(self):
        project = sample_project()
        row = project["rows"][0]
        row.update(quantity="1", price="0.005", increment="")
        project["rows"].append({**row, "id": "other"})
        project["settings"]["profit_percent"] = "0"
        result = calculate(project)
        self.assertEqual([r["amount"] for r in result["rows"]], ["0.01", "0.01"])
        self.assertEqual(result["totals"]["direct"], "0.02")

    def test_margin_not_markup_discount_then_tax_and_explicit_cost_bases(self):
        project = sample_project()
        project["rows"][0].update(quantity="1", price="1000", increment="")
        project["settings"].update(overhead_percent="10", reserve_percent="5", pricing_mode="margin", profit_percent="30", discount_percent="10", tax_mode="add", tax_percent="20")
        result = calculate(project)
        expected = {"direct": "1000.00", "overhead": "100.00", "reserve": "55.00", "cost": "1155.00", "sale_before_discount": "1650.00", "discount": "165.00", "sale_net": "1485.00", "tax": "297.00", "total": "1782.00", "profit": "330.00", "margin_percent": "22.22", "unit_total": "89.10"}
        self.assertEqual(result["totals"], expected)

    def test_partial_rows_never_become_customer_total(self):
        project = sample_project()
        project["rows"].append({**project["rows"][0], "id": "unknown", "name": "Доставка", "price": ""})
        result = calculate(project)
        self.assertEqual(result["totals"]["direct"], "5130.00")
        self.assertEqual({key: value for key, value in result["totals"].items() if key != "direct"}, {key: None for key in result["totals"] if key != "direct"})
        self.assertIsNone(result["rows"][1]["amount"])
        self.assertFalse(result["complete"])
        self.assertTrue(any("Доставка" in message and "цена" in message for message in result["missing"]))

    def test_explicit_zero_is_known_but_blank_quantity_is_not(self):
        project = sample_project()
        project["rows"][0]["price"] = "0"
        result = calculate(project)
        self.assertEqual(result["totals"]["total"], "0.00")
        self.assertTrue(result["complete"])
        self.assertIsNone(result["totals"]["margin_percent"])
        project["rows"][0]["quantity"] = ""
        result = calculate(project)
        self.assertIsNone(result["totals"]["total"])
        self.assertFalse(result["complete"])

    def test_missing_tax_preserves_net_but_total_unknown(self):
        for tax_mode in ("unknown", "add"):
            with self.subTest(tax_mode=tax_mode):
                project = sample_project()
                project["settings"].update(tax_mode=tax_mode, tax_percent="")
                result = calculate(project)
                self.assertEqual(result["totals"]["sale_net"], "6669.00")
                self.assertIsNone(result["totals"]["tax"])
                self.assertIsNone(result["totals"]["total"])
                self.assertFalse(result["complete"])

    def test_historical_and_conflicts_cannot_be_checked_into_complete(self):
        for status in ("historical_snapshot", "conflict"):
            with self.subTest(status=status):
                project = sample_project()
                project["rows"][0]["source_status"] = status
                result = calculate(project)
                self.assertFalse(result["complete"])
                self.assertTrue(any("manual" in reason for reason in result["missing"]))

    def test_missing_source_date_basis_scope_prevent_confirmation(self):
        project = sample_project()
        project["rows"][0].update(source="", price_date="", confirmed=False)
        project["settings"].update(cost_basis_confirmed=False, scope_confirmed=False)
        result = calculate(project)
        self.assertEqual(len(result["missing"]), 5)
        self.assertFalse(result["complete"])
        self.assertEqual(result["totals"]["total"], "6669.00")

    def test_empty_project_is_incomplete(self):
        result = calculate({})
        self.assertFalse(result["complete"])
        self.assertIsNone(result["totals"]["total"])

    def test_scope_checkbox_cannot_confirm_an_undescribed_composition(self):
        project = sample_project()
        project["project"]["notes"] = ""
        result = calculate(project)
        self.assertFalse(result["complete"])
        self.assertIn("Описание комплектации и условий заказа для заказчика.", result["missing"])

    def test_discount_can_produce_loss_or_zero_revenue(self):
        project = sample_project()
        project["settings"]["discount_percent"] = "100"
        result = calculate(project)
        self.assertEqual(result["totals"]["sale_net"], "0.00")
        self.assertEqual(result["totals"]["profit"], "-5130.00")
        self.assertIsNone(result["totals"]["margin_percent"])
        self.assertTrue(any("ниже" in reason for reason in result["warnings"]))

    def test_normalizes_comma_without_mutating_project(self):
        project = sample_project()
        project["rows"][0]["quantity"] = " 4,800 "
        original = copy.deepcopy(project)
        self.assertEqual(validate_project(project)["rows"][0]["quantity"], "4.8")
        calculate(project)
        self.assertEqual(project, original)

    def test_rejects_hostile_numeric_values(self):
        for value in (True, "NaN", "Infinity", "1e999999", "=1+1", "-1", "9" * 100000, "0.0000001", "1000000000001", [], {}):
            with self.subTest(value=str(value)[:40]):
                project = sample_project()
                project["rows"][0]["price"] = value
                with self.assertRaises(ValueError):
                    calculate(project)

    def test_rejects_invalid_percent_and_zero_or_negative_quantities(self):
        for key, value in (("quantity", "0"), ("quantity", "-1"), ("increment", "0"), ("minimum_charge", "-1")):
            project = sample_project()
            project["rows"][0][key] = value
            with self.assertRaises(ValueError):
                calculate(project)
        project = sample_project()
        project["settings"].update(pricing_mode="margin", profit_percent="100")
        with self.assertRaises(ValueError):
            calculate(project)

    def test_rejects_duplicate_ids_invalid_dates_boolean_strings_and_oversize_rows(self):
        project = sample_project()
        project["rows"] *= 2
        with self.assertRaises(ValueError):
            calculate(project)
        for updates in ({"price_date": "2026-02-30"}, {"confirmed": "false"}, {"source_status": "trusted"}, {"name": "\ud800"}):
            project = sample_project()
            project["rows"][0].update(updates)
            with self.assertRaises(ValueError):
                calculate(project)
        with self.assertRaises(ValueError):
            calculate({"rows": [{}] * 501})


class GeometryTests(unittest.TestCase):
    def basic(self):
        return {"width_mm": "600", "height_mm": "400", "quantity": "20", "sheet_width_mm": "2030", "sheet_height_mm": "3050", "edge_mm": "10", "gap_mm": "3", "bleed_mm": "0", "waste_percent": "10", "allow_rotate": True}

    def test_finished_area_perimeter_and_grid(self):
        result = geometry(self.basic())
        self.assertEqual(result["net_area_m2"], "4.8")
        self.assertEqual(result["billed_area_m2"], "5.28")
        self.assertEqual(result["perimeter_m"], "40")
        self.assertEqual(result["pieces_per_sheet"], 21)
        self.assertEqual(result["sheets"], 1)

    def test_rotation_increases_yield_and_sheet_rounds_up(self):
        payload = {"width_mm": "600", "height_mm": "400", "quantity": "5", "sheet_width_mm": "800", "sheet_height_mm": "1200", "allow_rotate": True}
        result = geometry(payload)
        self.assertEqual(result["pieces_per_sheet"], 4)
        self.assertEqual(result["sheets"], 2)
        self.assertIn("90°", result["layout_label"])
        result = geometry({**payload, "allow_rotate": False})
        self.assertEqual(result["pieces_per_sheet"], 3)

    def test_bleed_edges_and_gaps_change_fit(self):
        payload = {"width_mm": "100", "height_mm": "100", "quantity": "9", "sheet_width_mm": "330", "sheet_height_mm": "330", "edge_mm": "5", "gap_mm": "5", "bleed_mm": "2", "waste_percent": "0"}
        result = geometry(payload)
        # 3*104+2*5=322 > 320 usable, so only 2 by 2 fit.
        self.assertEqual(result["pieces_per_sheet"], 4)
        self.assertEqual(result["sheets"], 3)
        self.assertEqual(result["billed_area_m2"], "0.097344")

    def test_area_waste_not_double_applied_to_sheets(self):
        payload = self.basic()
        low = geometry(payload)
        high = geometry({**payload, "waste_percent": "500"})
        self.assertEqual(high["sheets"], low["sheets"])
        self.assertEqual(high["billed_area_m2"], "28.8")

    def test_no_sheet_only_returns_area(self):
        result = geometry({"width_mm": "1000", "height_mm": "1000", "quantity": "1"})
        self.assertEqual(result["net_area_m2"], "1")
        self.assertIsNone(result["pieces_per_sheet"])
        self.assertIsNone(result["sheets"])

    def test_fractional_millimeter_area_can_be_used_as_row_quantity(self):
        result = geometry({"width_mm": "600.5", "height_mm": "400.5", "quantity": "1"})
        # Exact 0.24050025 m² rounds upward to transferable 0.240501 m².
        self.assertEqual(result["net_area_m2"], "0.240501")
        self.assertEqual(result["billed_area_m2"], "0.240501")
        self.assertTrue(any("округлена вверх" in note for note in result["notes"]))
        project = sample_project()
        project["rows"][0].update(quantity=result["billed_area_m2"], increment="", price="100")
        self.assertEqual(calculate(project)["rows"][0]["amount"], "24.05")

    def test_rejects_no_fit_partial_sheet_fractional_and_unbounded_input(self):
        for changes in ({"width_mm": "5000"}, {"edge_mm": "2000"}, {"sheet_width_mm": ""}, {"quantity": "1.5"}, {"gap_mm": "-1"}, {"allow_rotate": "false"}, {"width_mm": "1e10000"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                geometry({**self.basic(), **changes})


if __name__ == "__main__":
    unittest.main()
