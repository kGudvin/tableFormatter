import re
from dataclasses import dataclass
from typing import Any

PURCHASE_NUMBER_ALIASES = {"Номер аукциона", "Номер закупки"}
PURCHASE_NUMBER_RE = re.compile(r"\d{11,20}")
TARGET_COLUMNS = ["Победная ставка", "Кто выиграл", "ИНН победителя", "Поставляемый товар"]
REQUIRED_COLUMNS = [
    "Номер аукциона",
    "Дата окончания приёма заявок",
    "Объект закупки",
    "Статус",
    "Победная ставка",
    "Кто выиграл",
    "Поставляемый товар",
]
SERVICE_COLUMNS = [
    "ИНН победителя",
    "Текущая цена контракта",
    "Изменение контракта",
    "Ко вниманию",
    "Автостатус",
    "Ручная фиксация",
    "Дата последней проверки",
    "Дата последнего автообновления",
    "Источник протокола",
    "Источник контракта",
    "Источник спецификации",
    "Ссылка на реестровую запись",
    "Ошибка обработки",
    "Первоначальный победитель",
]
REVIEW_COLUMNS = [
    "Номер закупки",
    "Причина",
    "Статус проверки",
    "Данные",
    "Дата создания",
    "Дата обновления",
]


@dataclass(frozen=True)
class SheetSchema:
    header_row: int
    columns: dict[str, int]
    original_headers: list[str]

    def column(self, name: str) -> int:
        try:
            return self.columns[name]
        except KeyError as exc:
            msg = f"Required sheet column is missing: {name}"
            raise MissingColumnError(msg) from exc

    @property
    def purchase_number_column(self) -> int:
        for alias in PURCHASE_NUMBER_ALIASES:
            if alias in self.columns:
                return self.columns[alias]
        msg = f"Missing purchase number column. Expected one of: {sorted(PURCHASE_NUMBER_ALIASES)}"
        raise MissingColumnError(msg)

    @property
    def missing_service_columns(self) -> list[str]:
        return [name for name in SERVICE_COLUMNS if name not in self.columns]


class MissingColumnError(RuntimeError):
    pass


def normalize_header(value: Any) -> str:
    return " ".join(str(value or "").replace("\n", " ").split()).strip()


def find_header_row(values: list[list[Any]], required: list[str] | None = None) -> SheetSchema:
    required = required or REQUIRED_COLUMNS
    best_score = -1
    best_index = -1
    best_headers: list[str] = []
    for index, row in enumerate(values, start=1):
        headers = [normalize_header(cell) for cell in row]
        score = sum(1 for col in required if col in headers)
        if not any(headers):
            continue
        if score > best_score:
            best_score = score
            best_index = index
            best_headers = headers
    if best_index < 0:
        raise MissingColumnError("Header row was not found")

    columns = {header: idx for idx, header in enumerate(best_headers, start=1) if header}
    missing_required = [col for col in required if col not in columns]
    has_purchase_number = any(alias in columns for alias in PURCHASE_NUMBER_ALIASES)
    if missing_required and not has_purchase_number:
        missing_required.append("Номер аукциона/Номер закупки")
    if missing_required and best_score == 0:
        msg = "Sheet header row does not contain required columns: " + ", ".join(missing_required)
        raise MissingColumnError(msg)
    return SheetSchema(header_row=best_index, columns=columns, original_headers=best_headers)


def is_working_row(row: list[Any], schema: SheetSchema) -> bool:
    purchase_idx = schema.purchase_number_column - 1
    if purchase_idx >= len(row):
        return False
    purchase_number = normalize_purchase_number(row[purchase_idx])
    if not purchase_number:
        return False
    if purchase_number.casefold() in {"номер аукциона", "номер закупки"}:
        return False
    if not is_purchase_number(purchase_number):
        return False
    return any(str(cell or "").strip() for cell in row)


def normalize_purchase_number(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def is_purchase_number(value: Any) -> bool:
    return bool(PURCHASE_NUMBER_RE.fullmatch(normalize_purchase_number(value)))
