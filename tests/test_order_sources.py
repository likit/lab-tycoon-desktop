"""Tests for SQLite/in-memory order-source selection and upload parsing."""

from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from zipfile import ZipFile

from app.system.order_sources import (
    InMemoryOrderSource,
    SQLiteOrderSource,
    UploadedOrderValidationError,
    get_active_order_source,
    load_uploaded_order_source,
    set_active_order_source,
    use_sqlite_order_source,
    use_uploaded_order_source,
)
from app.system.orchestration.rule_preview_ui import list_orders_flagged_by_current_rule
from app.system.signals.context_builder import build_rule_context
from app.system.signals.repository import build_rule_context_rows_for_orders


def write_test_xlsx(path: Path, headers: list[str], rows: list[list[str]]) -> None:
    shared_strings = [*headers]
    for row in rows:
        for value in row:
            if value not in shared_strings:
                shared_strings.append(value)

    def shared_index(value: str) -> int:
        return shared_strings.index(value)

    shared_xml = "".join(f"<si><t>{value}</t></si>" for value in shared_strings)
    row_xml = []
    all_rows = [headers, *rows]
    for row_number, row in enumerate(all_rows, start=1):
        cells = []
        for column_number, value in enumerate(row, start=1):
            column_letter = chr(ord("A") + column_number - 1)
            cells.append(
                f'<c r="{column_letter}{row_number}" t="s"><v>{shared_index(value)}</v></c>'
            )
        row_xml.append(f'<row r="{row_number}">{"".join(cells)}</row>')

    workbook_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets></workbook>'
    )
    rels_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
        'Target="worksheets/sheet1.xml"/>'
        '<Relationship Id="rId2" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/sharedStrings" '
        'Target="sharedStrings.xml"/>'
        '</Relationships>'
    )
    sheet_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<sheetData>{"".join(row_xml)}</sheetData></worksheet>'
    )
    content_types_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/worksheets/sheet1.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        '<Override PartName="/xl/sharedStrings.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/>'
        '</Types>'
    )
    root_rels_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="xl/workbook.xml"/>'
        '</Relationships>'
    )
    shared_strings_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        f'count="{len(shared_strings)}" uniqueCount="{len(shared_strings)}">{shared_xml}</sst>'
    )

    with ZipFile(path, "w") as workbook:
        workbook.writestr("[Content_Types].xml", content_types_xml)
        workbook.writestr("_rels/.rels", root_rels_xml)
        workbook.writestr("xl/workbook.xml", workbook_xml)
        workbook.writestr("xl/_rels/workbook.xml.rels", rels_xml)
        workbook.writestr("xl/worksheets/sheet1.xml", sheet_xml)
        workbook.writestr("xl/sharedStrings.xml", shared_strings_xml)


class OrderSourceTests(unittest.TestCase):
    def tearDown(self) -> None:
        use_sqlite_order_source()

    def test_load_valid_csv_into_memory_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "orders.csv"
            path.write_text(
                "order_id,patient_id,test_code,order_time,priority\n"
                "1,P001,CBC,2026-05-02T10:00:00,routine\n"
                "2,P001,CBC,2026-05-02T11:00:00,routine\n",
                encoding="utf-8",
            )
            source = load_uploaded_order_source(path)

        self.assertIsInstance(source, InMemoryOrderSource)
        self.assertEqual(source.count(), 2)
        self.assertEqual(source.get_order("1").order_items[0].test.code, "CBC")

    def test_groups_uploaded_rows_back_into_one_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "orders.csv"
            path.write_text(
                "order_id,patient_id,test_code,order_time,priority\n"
                "1,P001,CBC,2026-05-02T10:00:00,routine\n"
                "1,P001,HBA1C,2026-05-02T10:00:00,routine\n"
                "1,P001,CBC,2026-05-02T10:00:00,routine\n",
                encoding="utf-8",
            )
            source = load_uploaded_order_source(path)

        self.assertEqual(source.count(), 1)
        order = source.get_order("1")
        self.assertIsNotNone(order)
        self.assertEqual(
            [item.test.code for item in order.order_items],
            ["CBC", "HBA1C"],
        )

    def test_load_valid_excel_into_memory_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "orders.xlsx"
            write_test_xlsx(
                path,
                ["order_id", "patient_id", "test_code", "order_time", "priority"],
                [
                    ["1", "P001", "CBC", "2026-05-02T10:00:00", "routine"],
                    ["1", "P001", "LIPID", "2026-05-02T10:00:00", "routine"],
                ],
            )
            source = load_uploaded_order_source(path)

        self.assertEqual(source.count(), 1)
        self.assertEqual(source.get_order("1").customer_id, "P001")
        self.assertEqual(
            [item.test.code for item in source.get_order("1").order_items],
            ["CBC", "LIPID"],
        )

    def test_reject_missing_columns(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "orders.csv"
            path.write_text(
                "order_id,patient_id,test_code,order_time\n"
                "1,P001,CBC,2026-05-02T10:00:00\n",
                encoding="utf-8",
            )
            with self.assertRaises(UploadedOrderValidationError):
                load_uploaded_order_source(path)

    def test_reject_invalid_dates(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "orders.csv"
            path.write_text(
                "order_id,patient_id,test_code,order_time,priority\n"
                "1,P001,CBC,not-a-date,routine\n",
                encoding="utf-8",
            )
            with self.assertRaises(UploadedOrderValidationError):
                load_uploaded_order_source(path)

    def test_reject_invalid_priority_values(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "orders.csv"
            path.write_text(
                "order_id,patient_id,test_code,order_time,priority\n"
                "1,P001,CBC,2026-05-02T10:00:00,stat\n",
                encoding="utf-8",
            )
            with self.assertRaises(UploadedOrderValidationError):
                load_uploaded_order_source(path)

    def test_accepts_export_style_alias_headers(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "orders.csv"
            path.write_text(
                "ID,HN,Test,Ordered At,Priority\n"
                "1,P001,CBC,2026-05-02T10:00:00,routine\n",
                encoding="utf-8",
            )
            source = load_uploaded_order_source(path)

        order = source.get_order("1")
        self.assertEqual(order.customer_id, "P001")
        self.assertEqual(order.order_items[0].test.code, "CBC")

    def test_signal_layer_works_with_in_memory_orders(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "orders.csv"
            path.write_text(
                "order_id,patient_id,test_code,order_time,priority\n"
                "1,P001,CBC,2026-05-02T10:00:00,routine\n"
                "2,P001,CBC,2026-05-02T11:00:00,routine\n",
                encoding="utf-8",
            )
            source = load_uploaded_order_source(path)

        rows = build_rule_context_rows_for_orders(order_source=source)
        self.assertEqual(len(rows), 2)
        self.assertFalse(rows[0]["context"]["payload"]["duplicate_candidate"])
        self.assertTrue(rows[1]["context"]["payload"]["duplicate_candidate"])

    def test_flagged_order_evaluation_works_with_in_memory_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "orders.csv"
            path.write_text(
                "order_id,patient_id,test_code,order_time,priority\n"
                "1,P001,CBC,2026-05-02T10:00:00,routine\n"
                "2,P001,CBC,2026-05-02T11:00:00,routine\n",
                encoding="utf-8",
            )
            load_uploaded_order_source(path)
            use_uploaded_order_source()
            result = list_orders_flagged_by_current_rule()

        self.assertTrue(result["ok"])
        self.assertEqual([order["order_id"] for order in result["orders"]], ["2"])

    def test_can_switch_back_to_sqlite_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "orders.csv"
            path.write_text(
                "order_id,patient_id,test_code,order_time,priority\n"
                "1,P001,CBC,2026-05-02T10:00:00,routine\n",
                encoding="utf-8",
            )
            load_uploaded_order_source(path)
            use_uploaded_order_source()
            self.assertIsInstance(get_active_order_source(), InMemoryOrderSource)

        source = use_sqlite_order_source()

        self.assertIsInstance(source, SQLiteOrderSource)
        self.assertIsInstance(get_active_order_source(), SQLiteOrderSource)


if __name__ == "__main__":
    unittest.main()
