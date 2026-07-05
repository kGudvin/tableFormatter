import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from lxml import html

from app.domain.models import ProductItem
from app.domain.money import parse_money

_INN_RE = re.compile(r"\b\d{10}(?:\d{2})?\b")
_PRICE_RE = re.compile(r"(?:цен[аы]|предложени[ея]|сумм[аы])[^0-9]{0,80}([0-9][0-9\s\u00a0]*[,.]\d{2})", re.I)
_PARTY_MARKERS = [
    "ооо",
    "ао",
    "пао",
    "зао",
    "оао",
    "ип ",
    "индивидуальный предприниматель",
    "общество",
    "учреждение",
    "предприятие",
    "организация",
    "компания",
]
_SECTION_MARKERS = [
    "объект закупки",
    "заказчик",
    "начальная цена",
    "дата окончания",
    "способ определения",
    "электронная площадка",
    "место поставки",
    "преимущества",
    "требования",
    "закупка заверш",
]


@dataclass(frozen=True)
class ContractSummary:
    reestr_number: str
    supplier_name: str | None
    price: str | None
    contract_url: str | None = None


def parse_final_protocol_text(text: str) -> tuple[str | None, str | None, str | None, str | None] | None:
    compact = _compact(text)
    status = _find_status(compact)
    winner = _find_after(compact, ["Победитель", "Участник закупки, с которым заключается контракт", "Поставщик"])
    inn = _first_inn(compact)
    price = _first_price(compact)
    if status or winner or inn or price:
        return winner, inn, price, status
    return None


def parse_contract_text(text: str) -> tuple[str | None, str | None, str | None] | None:
    compact = _compact(text)
    supplier = _find_after(compact, ["Поставщик", "Исполнитель", "Подрядчик"])
    inn = _first_inn(compact)
    price = _first_price(compact)
    if supplier or inn or price:
        return supplier, inn, price
    return None


def parse_supplier_results_contract(html_text: str) -> ContractSummary | None:
    doc = html.fromstring(html_text)
    for link in doc.xpath('//a[contains(@href, "contractCard/common-info.html")]'):
        href = str(link.get("href") or "")
        reestr_match = re.search(r"reestrNumber=(\d+)", href)
        if reestr_match is None:
            continue
        row = _nearest_parent(link, "tr")
        if row is None:
            continue
        cells = [_clean_text(cell.text_content()) for cell in row.xpath("./td")]
        if len(cells) < 5:
            continue
        return ContractSummary(
            reestr_number=reestr_match.group(1),
            supplier_name=cells[2] or None,
            price=cells[3] or None,
            contract_url=href,
        )
    return None


def parse_contract_supplier_details(html_text: str) -> tuple[str | None, str | None] | None:
    doc = html.fromstring(html_text)
    for row in doc.xpath('//tr[contains(concat(" ", normalize-space(@class), " "), " tableBlock__row ")]'):
        cells = [_clean_text(cell.text_content()) for cell in row.xpath("./td")]
        if not cells:
            continue
        first = cells[0]
        if "поставщик" not in first.casefold():
            continue
        inn = _first_inn(first)
        supplier = re.sub(r"\s+ИНН\s*:.*$", "", first, flags=re.I).strip(" .,:;")
        supplier = re.sub(r"\s+Поставщик.*$", "", supplier, flags=re.I).strip(" .,:;")
        return supplier or None, inn
    return None


def parse_contract_products_html(html_text: str, source_url: str | None = None) -> list[ProductItem]:
    doc = html.fromstring(html_text)
    products: list[ProductItem] = []
    for row in doc.xpath('//tr[contains(concat(" ", normalize-space(@class), " "), " tableBlock__row ")]'):
        if "hidden" in str(row.get("class") or ""):
            continue
        cells = [_clean_text(cell.text_content()) for cell in row.xpath("./td")]
        if len(cells) < 7 or cells[3].casefold() != "товар":
            continue
        description = cells[1]
        ktru = cells[2]
        quantity, unit = _parse_quantity_cell(cells[4])
        products.append(
            ProductItem(
                position=len(products) + 1,
                country=_find_country(description),
                manufacturer=_find_labeled(description, ["производитель"]),
                trademark=_find_labeled(description, ["товарный знак", "торговая марка"]),
                model=_product_name(description, ktru),
                registry_number=_find_registry_number(description),
                quantity=quantity,
                unit=unit,
                source_url=source_url,
            )
        )
    return products


def parse_products_from_text(text: str, source_url: str | None = None) -> list[ProductItem]:
    lines = [line.strip(" \t|") for line in text.splitlines() if line.strip()]
    products: list[ProductItem] = []
    for line in lines:
        lowered = line.casefold()
        if not any(token in lowered for token in ["страна", "производител", "модель", "реестров"]):
            continue
        registry = _find_registry_number(line)
        quantity, unit = _find_quantity(line)
        products.append(
            ProductItem(
                position=len(products) + 1,
                country=_find_labeled(line, ["страна происхождения", "страна"]),
                manufacturer=_find_labeled(line, ["производитель"]),
                trademark=_find_labeled(line, ["товарный знак", "торговая марка"]),
                model=_find_labeled(line, ["модель"]),
                registry_number=registry,
                quantity=quantity,
                unit=unit,
                source_url=source_url,
            )
        )
    return products


def _compact(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text)).strip()


def _clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("\u00a0", " ")).strip()


def _nearest_parent(node: Any, tag: str) -> Any | None:
    current = node
    while hasattr(current, "getparent"):
        current = current.getparent()
        if current is None:
            return None
        if getattr(current, "tag", None) == tag:
            return current
    return None


def _first_inn(text: str) -> str | None:
    match = _INN_RE.search(text)
    return match.group(0) if match else None


def _first_price(text: str) -> str | None:
    match = _PRICE_RE.search(text)
    if match:
        return match.group(1)
    parsed = parse_money(text)
    return str(parsed) if parsed is not None else None


def _find_status(text: str) -> str | None:
    statuses = [
        "Закупка отменена",
        "Закупка не состоялась",
        "Не подано ни одной заявки",
        "Все заявки отклонены",
        "Подана одна заявка",
        "Контракт не заключён",
        "Победитель уклонился",
        "Результаты аннулированы",
    ]
    folded = text.casefold()
    return next((status for status in statuses if status.casefold() in folded), None)


def _find_after(text: str, labels: list[str]) -> str | None:
    stops = [
        "ИНН",
        "Цена",
        "Сумма",
        "Объект закупки",
        "Заказчик",
        "Начальная цена",
        "Дата окончания",
        "Способ определения",
        "Электронная площадка",
        "Место поставки",
        "Преимущества",
        "Требования",
        "Закупка заверш",
    ]
    stop_pattern = "|".join(re.escape(stop) for stop in stops)
    for label in labels:
        pattern = rf"{re.escape(label)}\s*:?\s*([А-ЯЁA-Z0-9\"'«» .,\-()]+?)(?:\s+(?:{stop_pattern})|$)"
        match = re.search(pattern, text, re.I)
        if match:
            value = match.group(1).strip(" .,:;")
            if _looks_like_party(value):
                return value[:300]
    return None


def _looks_like_party(value: str) -> bool:
    folded = value.casefold()
    if not value or len(value) < 4:
        return False
    if any(marker in folded for marker in _SECTION_MARKERS):
        return False
    if "заверш" in folded and not any(marker in folded for marker in _PARTY_MARKERS):
        return False
    return bool(_INN_RE.search(value) or any(marker in folded for marker in _PARTY_MARKERS))


def _find_labeled(text: str, labels: list[str]) -> str | None:
    stop_labels = [
        "страна происхождения",
        "страна",
        "производитель",
        "товарный знак",
        "торговая марка",
        "модель",
        "реестровый номер",
        "количество",
        "наличие",
        "возможность",
    ]
    stop_pattern = "|".join(re.escape(label) for label in stop_labels)
    for label in labels:
        pattern = rf"{re.escape(label)}\s*:?\s*(.+?)(?=\s+(?:{stop_pattern})\s*:|[;|,\n]|$)"
        match = re.search(pattern, text, re.I)
        if match:
            return match.group(1).strip(" .,:;")[:200]
    return None


def _find_registry_number(text: str) -> str | None:
    match = re.search(r"(?:реестров(?:ый|ого)? номер|РРПП|№)\s*:?\s*([A-Za-zА-Яа-я0-9./\-]+)", text, re.I)
    return match.group(1).strip() if match else None


def _find_country(text: str) -> str | None:
    match = re.search(
        r"(?:страна происхождения|страна)\s*:?\s*([А-ЯЁа-яёA-Za-z .-]+(?:\(\d+\))?)",
        text,
        re.I,
    )
    if not match:
        return None
    return match.group(1).strip(" .,:;")[:200]


def _find_quantity(text: str) -> tuple[Decimal | None, str | None]:
    match = re.search(r"(?:кол-?во|количество)\s*:?\s*([0-9]+(?:[,.][0-9]+)?)\s*([А-Яа-яA-Za-z.]+)?", text, re.I)
    if not match:
        return None, None
    quantity = Decimal(match.group(1).replace(",", "."))
    return quantity, match.group(2)


def _parse_quantity_cell(text: str) -> tuple[Decimal | None, str | None]:
    match = re.search(r"([0-9]+(?:[,.][0-9]+)?)\s*([А-Яа-яA-Za-z.]+)?", text)
    if not match:
        return None, None
    return Decimal(match.group(1).replace(",", ".")), match.group(2)


def _product_name(description: str, ktru: str) -> str | None:
    description = re.sub(r"^\d+\.\s*", "", description).strip()
    description = re.split(r"\s+Товарный знак\s*:", description, maxsplit=1, flags=re.I)[0]
    description = re.split(r"\s+Страна происхождения\s*:", description, maxsplit=1, flags=re.I)[0]
    description = description.strip(" .,:;")
    if description:
        return description[:200]
    if ktru:
        return re.split(r"\s+\(", ktru, maxsplit=1)[0][:200]
    return None
