from __future__ import annotations

import argparse
import re
import socket
import ssl
import tempfile
import urllib.request
from pathlib import Path
from typing import Any, cast

DEFAULT_HOST = "zakupki.gov.ru"
DEFAULT_OUTPUT = Path("certs/eis-ca-bundle.pem")


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a CA bundle for the EIS public site.")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    chain = build_bundle(args.host)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("\n".join(chain) + "\n", encoding="ascii")
    print(f"written {args.output} ({len(chain)} certificates)")


def build_bundle(host: str) -> list[str]:
    pem = fetch_server_certificate(host)
    bundle: list[str] = []
    seen: set[str] = set()
    while pem not in seen:
        seen.add(pem)
        bundle.append(pem)
        issuers = ca_issuer_urls(pem)
        if not issuers:
            break
        pem = download_certificate(issuers[0])
    return bundle


def fetch_server_certificate(host: str) -> str:
    context = ssl._create_unverified_context()
    with (
        socket.create_connection((host, 443), timeout=20) as sock,
        context.wrap_socket(sock, server_hostname=host) as tls,
    ):
        der = tls.getpeercert(binary_form=True)
    if der is None:
        raise RuntimeError(f"{host} did not return a certificate")
    return ssl.DER_cert_to_PEM_cert(der)


def ca_issuer_urls(pem: str) -> list[str]:
    decoded = decode_pem(pem)
    raw_values = decoded.get("caIssuers", ())
    values = (raw_values,) if isinstance(raw_values, str) else raw_values
    if not isinstance(values, tuple):
        return []
    return [str(value) for value in values if str(value).startswith(("http://", "https://"))]


def decode_pem(pem: str) -> dict[str, Any]:
    with tempfile.NamedTemporaryFile("w", encoding="ascii", suffix=".pem", delete=False) as tmp:
        tmp.write(pem)
        tmp_path = Path(tmp.name)
    try:
        return cast(dict[str, Any], ssl._ssl._test_decode_cert(str(tmp_path)))  # type: ignore[attr-defined]
    finally:
        tmp_path.unlink(missing_ok=True)


def download_certificate(url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": "procurement-autofill/0.1"})
    with urllib.request.urlopen(request, timeout=20) as response:
        content = response.read()
    if b"-----BEGIN CERTIFICATE-----" in content:
        text = content.decode("ascii")
        match = re.search(
            r"-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----",
            text,
            flags=re.S,
        )
        if match is None:
            raise RuntimeError(f"{url} did not contain a PEM certificate")
        return match.group(0)
    return ssl.DER_cert_to_PEM_cert(content)


if __name__ == "__main__":
    main()
