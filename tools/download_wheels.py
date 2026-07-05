from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, cast
from urllib.request import Request, urlopen

from packaging.markers import default_environment
from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from packaging.tags import sys_tags
from packaging.utils import parse_wheel_filename
from packaging.version import InvalidVersion, Version


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("requirements", nargs="+", type=Path)
    parser.add_argument("--dest", type=Path, default=Path("tmp/wheels"))
    args = parser.parse_args()
    args.dest.mkdir(parents=True, exist_ok=True)

    resolver = WheelResolver(args.dest)
    for path in args.requirements:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or line.startswith("-r "):
                continue
            resolver.add_requirement(Requirement(line))
    resolver.resolve_all()


class WheelResolver:
    def __init__(self, dest: Path) -> None:
        self.dest = dest
        self.requirements: dict[str, list[Requirement]] = {}
        self.resolved: dict[str, Version] = {}
        self.compatible_tags = list(sys_tags())
        self.environment = cast(dict[str, str], default_environment())

    def add_requirement(self, requirement: Requirement) -> None:
        if requirement.marker and not requirement.marker.evaluate(self.environment):
            return
        key = canonical(requirement.name)
        self.requirements.setdefault(key, []).append(requirement)

    def resolve_all(self) -> None:
        while True:
            pending = [name for name in self.requirements if name not in self.resolved]
            if not pending:
                return
            self.resolve_name(pending[0])

    def resolve_name(self, name: str) -> None:
        specs = [req.specifier for req in self.requirements[name]]
        version = self.choose_version(name, specs)
        meta = fetch_json(f"https://pypi.org/pypi/{name}/{version}/json")
        wheel = self.choose_wheel(name, version, cast(list[dict[str, object]], meta["urls"]))
        target = self.dest / wheel["filename"]
        if not target.exists():
            print(f"download {wheel['filename']}")
            target.write_bytes(fetch_bytes(wheel["url"]))
        self.resolved[name] = version
        info = cast(dict[str, Any], meta["info"])
        for raw in info.get("requires_dist") or []:
            requirement = Requirement(str(raw))
            self.add_requirement(requirement)

    def choose_version(self, name: str, specs: list[SpecifierSet]) -> Version:
        meta = fetch_json(f"https://pypi.org/pypi/{name}/json")
        versions: list[Version] = []
        releases = cast(dict[str, object], meta["releases"])
        for raw_version in releases:
            try:
                version = Version(raw_version)
            except InvalidVersion:
                continue
            if not version.is_prerelease:
                versions.append(version)
        versions.sort(reverse=True)
        for version in versions:
            if all(version in spec for spec in specs):
                return version
        raise RuntimeError(f"No version satisfies {name}: {specs}")

    def choose_wheel(self, name: str, version: Version, files: list[dict[str, object]]) -> dict[str, str]:
        best: tuple[int, dict[str, str]] | None = None
        tag_order = {tag: index for index, tag in enumerate(self.compatible_tags)}
        for file in files:
            filename = str(file["filename"])
            if str(file.get("packagetype")) != "bdist_wheel" or not filename.endswith(".whl"):
                continue
            _, wheel_version, _, tags = parse_wheel_filename(filename)
            if wheel_version != version:
                continue
            ranks = [tag_order[tag] for tag in tags if tag in tag_order]
            if not ranks:
                continue
            rank = min(ranks)
            candidate = {"filename": filename, "url": str(file["url"])}
            if best is None or rank < best[0]:
                best = (rank, candidate)
        if best is None:
            raise RuntimeError(f"No compatible wheel for {name}=={version}")
        return best[1]


def canonical(name: str) -> str:
    return name.lower().replace("_", "-")


def fetch_json(url: str) -> dict[str, object]:
    with urlopen(Request(url, headers={"User-Agent": "wheelhouse-downloader"}), timeout=60) as response:
        return cast(dict[str, object], json.load(response))


def fetch_bytes(url: str) -> bytes:
    with urlopen(Request(url, headers={"User-Agent": "wheelhouse-downloader"}), timeout=120) as response:
        return cast(bytes, response.read())


if __name__ == "__main__":
    main()
