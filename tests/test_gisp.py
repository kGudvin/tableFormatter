from datetime import timedelta
from pathlib import Path

from openpyxl import Workbook

from app.sources.gisp import GispRegistry, build_registry_index, normalize_registry_number


def _registry_workbook(path: Path) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["Время выгрузки: test"])
    sheet.append([])
    sheet.append(["Предприятие", "Реестровый номер", "Наименование продукции"])
    sheet.append(["ООО Производитель", "РЭ-1234/56", "Компьютер"])
    workbook.save(path)


def test_normalize_registry_number_ignores_formatting() -> None:
    assert normalize_registry_number(" рэ-1234 / 56 ") == "РЭ123456"


async def test_registry_lookup_returns_manufacturer_from_official_export(tmp_path: Path) -> None:
    workbook_path = tmp_path / "registry.xlsx"
    index_path = tmp_path / "gisp-active-products.sqlite3"
    _registry_workbook(workbook_path)
    build_registry_index(workbook_path, index_path)

    registry = GispRegistry(tmp_path, refresh_after=timedelta(days=1))
    product = await registry.lookup("РЭ-1234/56")

    assert product is not None
    assert product.manufacturer == "ООО Производитель"
    assert product.product_name == "Компьютер"
