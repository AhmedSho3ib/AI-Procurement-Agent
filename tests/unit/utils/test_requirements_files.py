"""Dependency declaration consistency.

`pyproject.toml` is the source of truth; `requirements.txt` and
`requirements-dev.txt` exist for environments that consume a requirements file
directly. These tests fail if the three files drift apart, if a runtime pin
violates the range declared in pyproject, or if the pins stop being exact.

Without this guard the requirements files would silently rot - the classic way a
"just add a requirements.txt" request turns into a broken deployment.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest
from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from packaging.version import Version

from tests.conftest import REPO_ROOT

PYPROJECT = REPO_ROOT / "pyproject.toml"
RUNTIME_REQUIREMENTS = REPO_ROOT / "requirements.txt"
DEV_REQUIREMENTS = REPO_ROOT / "requirements-dev.txt"

_PIN_PATTERN = re.compile(
    r"^(?P<name>[A-Za-z0-9._-]+)(?P<extras>\[[A-Za-z0-9,._-]+\])?==(?P<version>[^\s;]+)$"
)


def _pyproject() -> dict:
    return tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))


def _normalise(name: str) -> str:
    return name.strip().lower().replace("_", "-")


def _requirement_lines(path: Path) -> list[str]:
    lines: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or line.startswith("-r "):
            continue
        lines.append(line)
    return lines


def _parse_pins(path: Path) -> dict[str, str]:
    """Return ``{normalised name: pinned version}`` for a requirements file."""
    pins: dict[str, str] = {}
    for line in _requirement_lines(path):
        match = _PIN_PATTERN.match(line)
        assert match is not None, f"{path.name}: not an exact pin: {line!r}"
        name = _normalise(match.group("name"))
        assert name not in pins, f"{path.name}: duplicate requirement {name}"
        pins[name] = match.group("version")
    return pins


def _declared_requirements() -> dict[str, Requirement]:
    declared: dict[str, Requirement] = {}
    for raw in _pyproject()["project"]["dependencies"]:
        requirement = Requirement(raw)
        declared[_normalise(requirement.name)] = requirement
    return declared


def _declared_dev_requirements() -> dict[str, Requirement]:
    declared: dict[str, Requirement] = {}
    for raw in _pyproject()["project"]["optional-dependencies"]["dev"]:
        requirement = Requirement(raw)
        declared[_normalise(requirement.name)] = requirement
    return declared


class TestRuntimeRequirements:
    def test_lists_exactly_the_runtime_dependencies(self) -> None:
        assert set(_parse_pins(RUNTIME_REQUIREMENTS)) == set(_declared_requirements())

    def test_every_pin_is_an_exact_version(self) -> None:
        assert len(_parse_pins(RUNTIME_REQUIREMENTS)) == len(_requirement_lines(RUNTIME_REQUIREMENTS))

    @pytest.mark.parametrize("name,version", sorted(_parse_pins(RUNTIME_REQUIREMENTS).items()))
    def test_pin_satisfies_the_range_declared_in_pyproject(self, name: str, version: str) -> None:
        requirement = _declared_requirements()[name]
        assert Version(version) in SpecifierSet(str(requirement.specifier)), (
            f"{name}=={version} violates pyproject's {requirement.specifier}"
        )

    def test_entries_are_alphabetical(self) -> None:
        names = [
            match.group("name").lower()
            for line in _requirement_lines(RUNTIME_REQUIREMENTS)
            if (match := _PIN_PATTERN.match(line))
        ]
        assert names == sorted(names)

    def test_declares_the_expected_providers(self) -> None:
        # A regression guard: the store layer needs an SDK for every store.
        pins = _parse_pins(RUNTIME_REQUIREMENTS)
        assert {"crewai", "google-genai", "tavily-python", "scrapegraph-py", "jinja2"} <= set(pins)

    def test_no_credentials_or_extra_index_urls(self) -> None:
        text = RUNTIME_REQUIREMENTS.read_text(encoding="utf-8")
        for marker in ("--extra-index-url", "--index-url", "http://", "https://", "api_key", "token="):
            assert marker not in text


class TestDevRequirements:
    def test_includes_the_runtime_requirements(self) -> None:
        assert "-r requirements.txt" in DEV_REQUIREMENTS.read_text(encoding="utf-8")

    def test_lists_exactly_the_dev_dependencies(self) -> None:
        assert set(_parse_pins(DEV_REQUIREMENTS)) == set(_declared_dev_requirements())

    @pytest.mark.parametrize("name,version", sorted(_parse_pins(DEV_REQUIREMENTS).items()))
    def test_pin_satisfies_the_range_declared_in_pyproject(self, name: str, version: str) -> None:
        requirement = _declared_dev_requirements()[name]
        assert Version(version) in SpecifierSet(str(requirement.specifier))

    def test_covers_the_quality_gates_used_in_ci(self) -> None:
        pins = _parse_pins(DEV_REQUIREMENTS)
        assert {"pytest", "pytest-cov", "ruff", "mypy"} <= set(pins)


class TestPyprojectConsistency:
    def test_runtime_floor_matches_the_declared_python_floor(self) -> None:
        project = _pyproject()["project"]
        assert project["requires-python"] == ">=3.11,<3.14"

    def test_dependencies_are_unique(self) -> None:
        names = [_normalise(Requirement(raw).name) for raw in _pyproject()["project"]["dependencies"]]
        assert len(names) == len(set(names))

    def test_dev_extras_are_disjoint_from_runtime(self) -> None:
        # A tool that ships at runtime must be a runtime dependency, not a dev extra.
        assert not set(_declared_dev_requirements()) & set(_declared_requirements())
