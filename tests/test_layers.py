"""Each tier imports only from the tiers above it: shared, then runtime, then qt."""

import ast
from pathlib import Path

import pytest

PACKAGE = Path(__file__).resolve().parents[1] / "bartleby"

#: What each tier may not import, by top-level name.
FORBIDDEN = {
    "shared": ("bartleby.runtime", "bartleby.qt", "bartlebot", "bartlebuddy", "PyQt6", "wizwalker", "wizsprinter"),
    "runtime": ("bartleby.qt", "bartlebot", "bartlebuddy", "PyQt6", "qasync"),
    "qt": ("bartlebot", "bartlebuddy", "wizwalker", "wizsprinter"),
}


def _imports(path: Path) -> set[str]:
    names = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module)
        elif isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
    return names


@pytest.mark.parametrize("tier", sorted(FORBIDDEN))
def test_a_tier_imports_nothing_below_it(tier: str):
    offences = [
        f"{path.relative_to(PACKAGE)} imports {name}"
        for path in sorted((PACKAGE / tier).rglob("*.py"))
        for name in _imports(path)
        if any(name == bad or name.startswith(bad + ".") for bad in FORBIDDEN[tier])
    ]
    assert not offences, offences
