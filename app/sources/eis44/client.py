from __future__ import annotations

import asyncio
import re
import ssl
from dataclasses import dataclass
from pathlib import Path
from time import monotonic

import certifi
import httpx

from app.domain.inn import normalize_inn
from app.domain.models import (
    ContractAmendment,
    ContractResult,
    FinalProtocolResult,
    ResultStatus,
    SpecificationResult,
)
from app.domain.money import parse_money
from app.parsers.documents import extract_text_from_bytes
from app.parsers.eis_text import (
    parse_contract_products_html,
    parse_contract_supplier_details,
    parse_contract_text,
    parse_final_protocol_text,
    parse_products_from_text,
    parse_supplier_results_contract,
)


class EisTemporaryUnavailable(RuntimeError):
    pass


@dataclass
class Eis44Source:
    base_url: str
    cache_dir: Path
    min_interval_seconds: float = 1.2
    timeout_seconds: float = 30.0
    retries: int = 3
    verify_ssl: bool = True
    ca_bundle: Path | None = None

    def __post_init__(self) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._last_request_at = 0.0
        self._client = httpx.AsyncClient(
            base_url=self.base_url.rstrip("/"),
            timeout=httpx.Timeout(self.timeout_seconds),
            headers={"User-Agent": "procurement-autofill/0.1 (+official-public-documents)"},
            follow_redirects=True,
            verify=self._verify_config(),
        )

    def _verify_config(self) -> ssl.SSLContext | bool:
        if not self.verify_ssl:
            return False
        if self.ca_bundle is not None:
            return ssl.create_default_context(cafile=str(self.ca_bundle))
        return ssl.create_default_context(cafile=certifi.where())

    async def aclose(self) -> None:
        await self._client.aclose()

    async def get_final_protocol(self, purchase_number: str) -> FinalProtocolResult | None:
        results_url = self._notice_url(purchase_number, "supplier-results")
        results_html = await self._get_text(results_url)
        if results_html is not None:
            summary = parse_supplier_results_contract(results_html)
            if summary is not None:
                return FinalProtocolResult(
                    purchase_number=purchase_number,
                    winner_name=summary.supplier_name,
                    winner_inn=None,
                    winning_price=parse_money(summary.price),
                    status=ResultStatus.NEEDS_REVIEW,
                    protocol_url=str(self._client.base_url) + results_url,
                )

        url = self._notice_url(purchase_number, "common-info")
        html = await self._get_text(url)
        if html is None:
            return None
        parsed = parse_final_protocol_text(html)
        if parsed is None:
            return None
        winner, inn, price, raw_status = parsed
        return FinalProtocolResult(
            purchase_number=purchase_number,
            winner_name=winner,
            winner_inn=normalize_inn(inn),
            winning_price=parse_money(price),
            status=ResultStatus.CONFIRMED if winner and normalize_inn(inn) else ResultStatus.NEEDS_REVIEW,
            protocol_url=str(self._client.base_url) + url,
            raw_status=raw_status,
        )

    async def get_contract(self, purchase_number: str) -> ContractResult | None:
        results_url = self._notice_url(purchase_number, "supplier-results")
        html = await self._get_text(results_url)
        if html is None:
            return None
        summary = parse_supplier_results_contract(html)
        if summary is None:
            parsed = parse_contract_text(html)
            if parsed is None:
                return None
            supplier, inn, price = parsed
            return ContractResult(
                purchase_number=purchase_number,
                supplier_name=supplier,
                supplier_inn=normalize_inn(inn),
                contract_price=parse_money(price),
                contract_url=str(self._client.base_url) + results_url,
            )

        supplier = summary.supplier_name
        inn = None
        target_url = self._contract_target_url(summary.reestr_number)
        target_html = await self._get_text(target_url)
        if target_html is not None:
            details = parse_contract_supplier_details(target_html)
            if details is not None:
                supplier = details[0] or supplier
                inn = details[1]
        return ContractResult(
            purchase_number=purchase_number,
            supplier_name=supplier,
            supplier_inn=normalize_inn(inn),
            contract_price=parse_money(summary.price),
            contract_url=str(self._client.base_url) + self._contract_common_url(summary.reestr_number),
        )

    async def get_specification(self, purchase_number: str) -> SpecificationResult | None:
        results_url = self._notice_url(purchase_number, "supplier-results")
        html = await self._get_text(results_url)
        if html is None:
            return None
        summary = parse_supplier_results_contract(html)
        if summary is not None:
            target_url = self._contract_target_url(summary.reestr_number)
            target_html = await self._get_text(target_url)
            if target_html is not None:
                spec_url = str(self._client.base_url) + target_url
                products = parse_contract_products_html(target_html, spec_url)
                if products:
                    return SpecificationResult(
                        purchase_number=purchase_number,
                        products=products,
                        specification_url=spec_url,
                        version="contract",
                    )

        documents_url = self._notice_url(purchase_number, "documents")
        documents_html = await self._get_text(documents_url)
        if documents_html is None:
            return None
        spec_link = self._first_specification_link(documents_html)
        text = documents_html
        spec_url = str(self._client.base_url) + documents_url
        if spec_link:
            content = await self._get_bytes(spec_link)
            if content:
                text = extract_text_from_bytes(spec_link, content)
                spec_url = self._absolute_url(spec_link)
        products = parse_products_from_text(text, spec_url)
        if not products:
            return None
        return SpecificationResult(
            purchase_number=purchase_number,
            products=products,
            specification_url=spec_url,
            version="current",
        )

    async def get_amendments(self, purchase_number: str) -> list[ContractAmendment]:
        return []

    def _notice_url(self, purchase_number: str, page: str) -> str:
        number = re.sub(r"\D", "", purchase_number)
        return f"/epz/order/notice/view/{page}.html?regNumber={number}"

    def _contract_common_url(self, reestr_number: str) -> str:
        return f"/epz/contract/contractCard/common-info.html?reestrNumber={reestr_number}"

    def _contract_target_url(self, reestr_number: str) -> str:
        return f"/epz/contract/contractCard/payment-info-and-target-of-order.html?reestrNumber={reestr_number}"

    async def _rate_limit(self) -> None:
        elapsed = monotonic() - self._last_request_at
        if elapsed < self.min_interval_seconds:
            await asyncio.sleep(self.min_interval_seconds - elapsed)
        self._last_request_at = monotonic()

    async def _get_text(self, url: str) -> str | None:
        content = await self._get_bytes(url)
        if content is None:
            return None
        return content.decode("utf-8", errors="replace")

    async def _get_bytes(self, url: str) -> bytes | None:
        cache_path = self.cache_dir / self._cache_name(url)
        if cache_path.exists():
            return cache_path.read_bytes()
        delay = 1.0
        for attempt in range(1, self.retries + 1):
            try:
                await self._rate_limit()
                response = await self._client.get(url)
                if response.status_code == 404:
                    return None
                if response.status_code in {429, 500, 502, 503, 504}:
                    raise EisTemporaryUnavailable(f"EIS temporary status {response.status_code}")
                response.raise_for_status()
                cache_path.write_bytes(response.content)
                return response.content
            except (httpx.TimeoutException, httpx.TransportError, EisTemporaryUnavailable):
                if attempt == self.retries:
                    raise
                await asyncio.sleep(delay)
                delay *= 2
        return None

    def _cache_name(self, url: str) -> str:
        safe = re.sub(r"[^a-zA-Z0-9_.-]+", "_", url.strip("/"))[:180]
        return safe or "root"

    def _absolute_url(self, url: str) -> str:
        return url if url.startswith(("http://", "https://")) else str(self._client.base_url) + url

    def _first_contract_link(self, html: str) -> str | None:
        return self._first_link(html, ["контракт", "contract"])

    def _first_specification_link(self, html: str) -> str | None:
        return self._first_link(html, ["спецификац", "приложен", "xlsx", "docx", "pdf"])

    def _first_link(self, html: str, needles: list[str]) -> str | None:
        for href, text in re.findall(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', html, re.I | re.S):
            haystack = f"{href} {re.sub(r'<[^>]+>', ' ', text)}".casefold()
            if any(needle in haystack for needle in needles):
                link = str(href)
                if link.startswith(("http://", "https://")):
                    return link
                return link if link.startswith("/") else "/" + link
        return None
