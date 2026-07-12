import shutil
import ssl
from pathlib import Path

import certifi

from app.domain.models import ProductItem
from app.sources.eis44 import Eis44Source
from app.sources.gisp import GISP_REGISTRY_PAGE_URL, GispProduct


class _FakeGisp:
    async def lookup(self, registry_number: str) -> GispProduct | None:
        assert registry_number == "10512345"
        return GispProduct(registry_number="10512345", manufacturer="ООО Производитель")


async def test_eis_client_uses_custom_ca_bundle(tmp_path: Path) -> None:
    bundle = tmp_path / "eis-ca.pem"
    shutil.copyfile(certifi.where(), bundle)
    source = Eis44Source(base_url="https://example.test", cache_dir=tmp_path, ca_bundle=bundle)

    try:
        assert isinstance(source._verify_config(), ssl.SSLContext)
    finally:
        await source.aclose()


async def test_eis_client_can_disable_tls_verification(tmp_path: Path) -> None:
    source = Eis44Source(base_url="https://example.test", cache_dir=tmp_path, verify_ssl=False)

    try:
        assert source._verify_config() is False
    finally:
        await source.aclose()


async def test_eis_client_uses_certifi_by_default(tmp_path: Path) -> None:
    source = Eis44Source(base_url="https://example.test", cache_dir=tmp_path)

    try:
        assert isinstance(source._verify_config(), ssl.SSLContext)
    finally:
        await source.aclose()


async def test_eis_client_keeps_absolute_links(tmp_path: Path) -> None:
    source = Eis44Source(base_url="https://zakupki.gov.ru", cache_dir=tmp_path)

    try:
        html = '<a href="https://zakupki.gov.ru/44fz/file.pdf">Спецификация</a>'
        assert source._first_specification_link(html) == "https://zakupki.gov.ru/44fz/file.pdf"
        assert source._absolute_url("https://zakupki.gov.ru/44fz/file.pdf") == "https://zakupki.gov.ru/44fz/file.pdf"
    finally:
        await source.aclose()


async def test_eis_client_enriches_product_without_trademark_from_gisp(tmp_path: Path) -> None:
    source = Eis44Source(base_url="https://zakupki.gov.ru", cache_dir=tmp_path)
    source._gisp = _FakeGisp()  # type: ignore[assignment]

    try:
        products = await source._enrich_products_from_gisp(
            [ProductItem(position=1, registry_number="10512345")]
        )
        assert products[0].manufacturer == "ООО Производитель"
        assert products[0].registry_url == GISP_REGISTRY_PAGE_URL
    finally:
        await source.aclose()
