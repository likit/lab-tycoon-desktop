"""Small order-source abstraction for SQLite and in-memory experiment data."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import csv
from pathlib import Path
from typing import Protocol
from zipfile import ZipFile
import xml.etree.ElementTree as ET

REQUIRED_UPLOADED_COLUMNS = {
    "order_id",
    "patient_id",
    "test_code",
    "order_time",
    "priority",
}
REQUIRED_UPLOADED_COLUMNS_ORDER = [
    "order_id",
    "patient_id",
    "test_code",
    "order_time",
    "priority",
]
VALID_PRIORITIES = {"urgent", "routine"}
UPLOADED_COLUMN_ALIASES = {
    "order_id": {"order_id", "id", "order id"},
    "patient_id": {"patient_id", "patient id", "hn"},
    "test_code": {"test_code", "test code", "test", "code"},
    "order_time": {"order_time", "order time", "ordered at"},
    "priority": {"priority"},
}


class OrderSource(Protocol):
    """Minimal contract for order-backed policy experiments."""

    label: str

    def list_orders(self) -> list[object]:
        """Return orders sorted for signal/rule evaluation."""

    def get_order(self, order_id: int | str) -> object | None:
        """Look up one order by id."""

    def count(self) -> int:
        """Return available order count."""


@dataclass(frozen=True, slots=True)
class InMemoryTest:
    code: str


@dataclass(frozen=True, slots=True)
class InMemoryOrderItem:
    test: InMemoryTest


@dataclass(frozen=True, slots=True)
class InMemoryCustomer:
    id: str
    hn: str
    fullname: str


@dataclass(slots=True)
class InMemoryOrder:
    id: str
    customer_id: str
    order_datetime: datetime
    order_items: list[InMemoryOrderItem]
    priority: str
    customer: InMemoryCustomer
    doctor: object | None = None
    approved_at: datetime | None = None
    rejected_at: datetime | None = None
    cancelled_at: datetime | None = None
    received_at: datetime | None = None


class SQLiteOrderSource:
    """Default database-backed order source."""

    label = "SQLite"

    def list_orders(self) -> list[object]:
        from sqlalchemy import select
        from sqlalchemy.orm import Session, selectinload

        from app.system.models import LabOrder, LabOrderItem, engine

        with Session(engine) as session:
            query = (
                select(LabOrder)
                .options(
                    selectinload(LabOrder.customer),
                    selectinload(LabOrder.doctor),
                    selectinload(LabOrder.order_items).selectinload(LabOrderItem.test),
                )
                .order_by(LabOrder.order_datetime, LabOrder.id)
            )
            return list(session.scalars(query))

    def get_order(self, order_id: int | str) -> object | None:
        from sqlalchemy import select
        from sqlalchemy.orm import Session, selectinload

        from app.system.models import LabOrder, LabOrderItem, engine

        with Session(engine) as session:
            return session.scalar(
                select(LabOrder)
                .where(LabOrder.id == int(order_id))
                .options(
                    selectinload(LabOrder.customer),
                    selectinload(LabOrder.doctor),
                    selectinload(LabOrder.order_items).selectinload(LabOrderItem.test),
                )
            )

    def count(self) -> int:
        return len(self.list_orders())


class InMemoryOrderSource:
    """Uploaded experiment orders kept outside SQLite."""

    label = "Uploaded file"

    def __init__(self, orders: list[InMemoryOrder], source_path: str) -> None:
        self._orders = sorted(orders, key=lambda order: (order.order_datetime, str(order.id)))
        self.source_path = source_path

    def list_orders(self) -> list[InMemoryOrder]:
        return list(self._orders)

    def get_order(self, order_id: int | str) -> InMemoryOrder | None:
        needle = str(order_id)
        for order in self._orders:
            if str(order.id) == needle:
                return order
        return None

    def count(self) -> int:
        return len(self._orders)


class UploadedOrderValidationError(ValueError):
    """Raised when uploaded experiment data is malformed."""


_active_order_source: OrderSource = SQLiteOrderSource()
_uploaded_order_source: InMemoryOrderSource | None = None


def get_active_order_source() -> OrderSource:
    return _active_order_source


def get_uploaded_order_source() -> InMemoryOrderSource | None:
    return _uploaded_order_source


def set_active_order_source(order_source: OrderSource) -> None:
    global _active_order_source
    _active_order_source = order_source


def use_sqlite_order_source() -> OrderSource:
    source = SQLiteOrderSource()
    set_active_order_source(source)
    return source


def use_uploaded_order_source() -> InMemoryOrderSource:
    if _uploaded_order_source is None:
        raise UploadedOrderValidationError("No uploaded order file has been loaded.")
    set_active_order_source(_uploaded_order_source)
    return _uploaded_order_source


def load_uploaded_order_source(path: str | Path) -> InMemoryOrderSource:
    global _uploaded_order_source
    file_path = Path(path)
    suffix = file_path.suffix.lower()
    if suffix == ".csv":
        raw_rows = _load_csv_rows(file_path)
    elif suffix == ".xlsx":
        raw_rows = _load_xlsx_rows(file_path)
    else:
        raise UploadedOrderValidationError("Only .csv and .xlsx files are supported.")

    orders = _build_in_memory_orders(raw_rows)
    source = InMemoryOrderSource(orders, str(file_path))
    _uploaded_order_source = source
    return source


def _load_csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise UploadedOrderValidationError("Uploaded CSV has no header row.")
        _validate_columns(reader.fieldnames)
        return [_normalize_row(row) for row in reader]


def _load_xlsx_rows(path: Path) -> list[dict[str, str]]:
    with ZipFile(path) as workbook:
        shared_strings = _load_shared_strings(workbook)
        sheet_rows = _load_sheet_rows(workbook, shared_strings)
    if not sheet_rows:
        raise UploadedOrderValidationError("Uploaded Excel file is empty.")
    header = sheet_rows[0]
    _validate_columns(header)
    return [
        _normalize_row(dict(zip(header, row, strict=False)))
        for row in sheet_rows[1:]
        if any(str(value).strip() for value in row)
    ]


def _load_shared_strings(workbook: ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in workbook.namelist():
        return []
    root = ET.fromstring(workbook.read("xl/sharedStrings.xml"))
    namespace = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    strings: list[str] = []
    for item in root.findall("x:si", namespace):
        text_parts = [node.text or "" for node in item.findall(".//x:t", namespace)]
        strings.append("".join(text_parts))
    return strings


def _load_sheet_rows(workbook: ZipFile, shared_strings: list[str]) -> list[list[str]]:
    if "xl/worksheets/sheet1.xml" not in workbook.namelist():
        raise UploadedOrderValidationError("Uploaded Excel file does not contain sheet1.")
    root = ET.fromstring(workbook.read("xl/worksheets/sheet1.xml"))
    namespace = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    rows: list[list[str]] = []
    for row_node in root.findall(".//x:sheetData/x:row", namespace):
        row_values: list[str] = []
        expected_index = 0
        for cell in row_node.findall("x:c", namespace):
            ref = cell.attrib.get("r", "")
            column_index = _column_index(ref)
            while expected_index < column_index:
                row_values.append("")
                expected_index += 1
            row_values.append(_cell_value(cell, shared_strings, namespace))
            expected_index += 1
        rows.append(row_values)
    return rows


def _cell_value(cell: ET.Element, shared_strings: list[str], namespace: dict[str, str]) -> str:
    cell_type = cell.attrib.get("t", "")
    if cell_type == "inlineStr":
        text_parts = [node.text or "" for node in cell.findall(".//x:t", namespace)]
        return "".join(text_parts)
    value_node = cell.find("x:v", namespace)
    raw = value_node.text if value_node is not None else ""
    if cell_type == "s" and raw:
        return shared_strings[int(raw)]
    return raw or ""


def _column_index(cell_ref: str) -> int:
    letters = "".join(character for character in cell_ref if character.isalpha())
    index = 0
    for character in letters:
        index = index * 26 + (ord(character.upper()) - ord("A") + 1)
    return max(index - 1, 0)


def _validate_columns(columns: list[str]) -> None:
    available = {_normalize_header(column) for column in columns}
    missing = sorted(
        canonical
        for canonical in REQUIRED_UPLOADED_COLUMNS
        if not any(alias in available for alias in UPLOADED_COLUMN_ALIASES[canonical])
    )
    if missing:
        raise UploadedOrderValidationError(
            f"Uploaded order data is missing required columns: {', '.join(missing)}"
        )


def _normalize_row(row: dict[str, object]) -> dict[str, str]:
    normalized = {
        _normalize_header(str(key)): str(value).strip()
        for key, value in row.items()
        if key is not None
    }
    canonical_row = {}
    for column in REQUIRED_UPLOADED_COLUMNS:
        canonical_row[column] = ""
        for alias in UPLOADED_COLUMN_ALIASES[column]:
            if alias in normalized and normalized[alias]:
                canonical_row[column] = normalized[alias]
                break
    return canonical_row


def _normalize_header(value: str) -> str:
    return " ".join(value.strip().lower().replace("_", " ").split())


def _build_in_memory_orders(rows: list[dict[str, str]]) -> list[InMemoryOrder]:
    grouped_rows: dict[tuple[str, str, str, str], list[str]] = {}
    for row in rows:
        order_id = _required_value(row, "order_id")
        patient_id = _required_value(row, "patient_id")
        order_time = _required_value(row, "order_time")
        priority = _required_value(row, "priority")
        test_code = _required_value(row, "test_code")
        group_key = (order_id, patient_id, order_time, priority)
        grouped_rows.setdefault(group_key, []).append(test_code)

    orders = []
    for (order_id, patient_id, order_time, priority), test_codes in grouped_rows.items():
        orders.append(
            _build_in_memory_order(
                {
                    "order_id": order_id,
                    "patient_id": patient_id,
                    "order_time": order_time,
                    "priority": priority,
                },
                test_codes,
            )
        )
    return orders


def _build_in_memory_order(row: dict[str, str], test_codes: list[str]) -> InMemoryOrder:
    order_id = _required_value(row, "order_id")
    patient_id = _required_value(row, "patient_id")
    order_datetime = _parse_order_time(_required_value(row, "order_time"))
    priority = _parse_priority(_required_value(row, "priority"))
    customer = InMemoryCustomer(
        id=patient_id,
        hn=patient_id,
        fullname=f"Uploaded Patient {patient_id}",
    )
    return InMemoryOrder(
        id=order_id,
        customer_id=patient_id,
        order_datetime=order_datetime,
        order_items=[
            InMemoryOrderItem(InMemoryTest(test_code))
            for test_code in sorted(dict.fromkeys(test_codes))
        ],
        priority=priority,
        customer=customer,
    )


def _required_value(row: dict[str, str], column: str) -> str:
    value = row.get(column, "").strip()
    if not value:
        raise UploadedOrderValidationError(f"Uploaded order row is missing {column}.")
    return value


def _parse_order_time(value: str) -> datetime:
    normalized = value.strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise UploadedOrderValidationError(f"Invalid order_time value: {value}") from exc


def _parse_priority(value: str) -> str:
    normalized = value.strip().lower()
    if normalized not in VALID_PRIORITIES:
        raise UploadedOrderValidationError(
            f"Invalid priority value: {value}. Expected urgent or routine."
        )
    return normalized
