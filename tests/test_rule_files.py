"""The family's rule files: each section exists once, and each root file stays short.

A rule moved into core is deleted from the product it left, and that product's file imports
core's instead. A section heading found in two CLAUDE.md files is a rule kept twice, which is
how the copies drift. The products are read from their checkouts beside core: a sibling
counts when its root CLAUDE.md imports core's, so a checkout of a branch from before the
move is left out, and only the first checkout of each product (by folder name) is read. Where
there is no sibling, as on CI, core is checked alone.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

CORE = Path(__file__).resolve().parents[1]

#: The import that makes a sibling one of the family.
CORE_IMPORT = "@../bartleby-core/CLAUDE.md"

#: Headings every repo's root file has, each about that repo.
STRUCTURAL = frozenset(
    {"quick start", "layout", "where the rest lives", "key conventions", "build / test / lint", "architecture", "rules"}
)

#: Folders no rule file is in.
_SKIP = {".git", ".venv", "__pycache__", "node_modules", "logs", "build", "dist", ".ruff_cache", ".pytest_cache"}

#: A root CLAUDE.md loads in full at the start of every session.
ROOT_LIMIT = 200

_HEADING = re.compile(r"^#{2,6} +(.+?)\s*$")


def family_repos() -> list[Path]:
    """Core, then each sibling checkout whose root CLAUDE.md imports core's, one per product."""
    repos, titles = [CORE], set()
    for sibling in sorted(p for p in CORE.parent.iterdir() if p.is_dir() and p != CORE):
        root = sibling / "CLAUDE.md"
        if not root.is_file():
            continue
        text = root.read_text(encoding="utf-8")
        title = next((line for line in text.splitlines() if line.startswith("# ")), sibling.name)
        if CORE_IMPORT in text and title not in titles:
            titles.add(title)
            repos.append(sibling)
    return repos


def rule_files(repo: Path) -> list[Path]:
    found = []
    for folder, dirs, files in os.walk(repo):
        dirs[:] = [d for d in dirs if d not in _SKIP]
        if "CLAUDE.md" in files:
            found.append(Path(folder) / "CLAUDE.md")
    return sorted(found)


def headings(path: Path) -> list[str]:
    """Every heading below the title, outside code fences, lowercased."""
    out, fenced = [], False
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.lstrip().startswith("```"):
            fenced = not fenced
        elif not fenced and (m := _HEADING.match(line)):
            out.append(m.group(1).lower())
    return out


def duplicate_headings(repos: list[Path]) -> dict[str, list[str]]:
    """Heading to the files it is in, for every heading in more than one place."""
    where: dict[str, list[str]] = {}
    for repo in repos:
        for path in rule_files(repo):
            label = f"{repo.name}/{path.relative_to(repo).as_posix()}"
            for heading in headings(path):
                if heading not in STRUCTURAL:
                    where.setdefault(heading, []).append(label)
    return {h: files for h, files in where.items() if len(files) > 1}


def test_every_rule_section_exists_once_across_the_family():
    assert duplicate_headings(family_repos()) == {}


@pytest.mark.parametrize("repo", family_repos(), ids=lambda p: p.name)
def test_every_root_rule_file_is_under_the_limit(repo):
    lines = len((repo / "CLAUDE.md").read_text(encoding="utf-8").splitlines())

    assert lines < ROOT_LIMIT, f"{repo.name}/CLAUDE.md is {lines} lines"


def test_a_heading_in_two_files_is_found_and_a_structural_one_is_not(tmp_path):
    for name, text in {
        "a/CLAUDE.md": "# A\n\n## Layout\n\n## Popup menus\n\n```\n## not a heading\n```\n",
        "b/CLAUDE.md": "# B\n\n## Layout\n\n## Popup Menus\n",
        "b/sub/CLAUDE.md": "## Only here\n\n## not a heading\n",
    }.items():
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text(text, encoding="utf-8")

    assert duplicate_headings([tmp_path / "a", tmp_path / "b"]) == {"popup menus": ["a/CLAUDE.md", "b/CLAUDE.md"]}
