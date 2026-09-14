"""Prompt library: agent personas and task instructions loaded from Markdown.

Every agent has exactly one prompt file under this package. Prompt files contain
``[SECTION]`` blocks so a single file fully describes an agent *and* its task,
which keeps the persona and the instructions for a role next to each other and
reviewable in pull requests.

Example::

    [ROLE]
    Search Engine Agent

    [TASK]
    Search for $product on $websites.

Variables use ``$name`` syntax (``string.Template``) rather than ``{name}`` so
prompts can contain literal JSON braces without escaping.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from string import Template

from src.utils.exceptions import ConfigurationError

__all__ = ["PromptLibrary", "PromptSections", "get_prompt_library"]

PROMPTS_DIR = Path(__file__).resolve().parent

_SECTION_PATTERN = re.compile(r"^\[([A-Z_]+)\]\s*$", re.MULTILINE)
_IDENTIFIER_PATTERN = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)")

_REQUIRED_SECTIONS = ("ROLE", "TASK", "EXPECTED_OUTPUT")


@dataclass(frozen=True, slots=True)
class PromptSections:
    """One rendered prompt file."""

    name: str
    role: str = ""
    goal: str = ""
    backstory: str = ""
    task: str = ""
    expected_output: str = ""

    def render(self, **variables: object) -> PromptSections:
        """Substitute ``$variables`` in every section.

        Raises:
            ConfigurationError: when the prompt references a variable the caller
                did not supply. Failing loudly here is far cheaper than debugging
                a ``$product`` literal inside an LLM prompt.
        """
        return PromptSections(
            name=self.name,
            role=_substitute(self.role, variables, self.name, "ROLE"),
            goal=_substitute(self.goal, variables, self.name, "GOAL"),
            backstory=_substitute(self.backstory, variables, self.name, "BACKSTORY"),
            task=_substitute(self.task, variables, self.name, "TASK"),
            expected_output=_substitute(self.expected_output, variables, self.name, "EXPECTED_OUTPUT"),
        )


class PromptLibrary:
    """Loads and caches prompt files from a directory of Markdown files."""

    def __init__(self, base_dir: Path | str | None = None) -> None:
        self._base_dir = Path(base_dir) if base_dir else PROMPTS_DIR
        if not self._base_dir.is_dir():
            raise ConfigurationError(
                "Prompt directory does not exist",
                context={"prompt_dir": str(self._base_dir)},
            )

    @property
    def base_dir(self) -> Path:
        return self._base_dir

    def available(self) -> list[str]:
        return sorted(path.stem for path in self._base_dir.glob("*.md"))

    @lru_cache(maxsize=32)  # noqa: B019 - library instances are long lived singletons
    def sections(self, name: str) -> PromptSections:
        """Return the raw (unrendered) sections for ``name``."""
        path = self._base_dir / f"{name}.md"
        if not path.is_file():
            raise ConfigurationError(
                "Prompt file not found",
                context={"name": name, "prompt_dir": str(self._base_dir), "available": self.available()},
            )

        raw = path.read_text(encoding="utf-8")
        blocks = self._split_sections(raw)
        missing = [section for section in _REQUIRED_SECTIONS if not blocks.get(section)]
        if missing:
            raise ConfigurationError(
                "Prompt file is missing required sections",
                context={"name": name, "missing": missing},
            )

        return PromptSections(
            name=name,
            role=blocks.get("ROLE", ""),
            goal=blocks.get("GOAL", ""),
            backstory=blocks.get("BACKSTORY", ""),
            task=blocks.get("TASK", ""),
            expected_output=blocks.get("EXPECTED_OUTPUT", ""),
        )

    def render(self, name: str, **variables: object) -> PromptSections:
        """Return ``name`` with ``$variables`` substituted."""
        return self.sections(name).render(**variables)

    @staticmethod
    def _split_sections(raw: str) -> dict[str, str]:
        matches = list(_SECTION_PATTERN.finditer(raw))
        blocks: dict[str, str] = {}
        for index, match in enumerate(matches):
            start = match.end()
            end = matches[index + 1].start() if index + 1 < len(matches) else len(raw)
            blocks[match.group(1)] = raw[start:end].strip()
        return blocks


def _substitute(text: str, variables: dict[str, object], name: str, section: str) -> str:
    if not text:
        return text
    rendered = Template(text).safe_substitute(**variables)
    unresolved = sorted(set(_IDENTIFIER_PATTERN.findall(rendered)))
    if unresolved:
        raise ConfigurationError(
            "Prompt has unresolved variables",
            context={"name": name, "section": section, "unresolved": unresolved},
        )
    return rendered


@lru_cache(maxsize=1)
def get_prompt_library() -> PromptLibrary:
    """Return the default prompt library (project ``prompts/`` package)."""
    return PromptLibrary()
