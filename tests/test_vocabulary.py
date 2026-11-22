"""One vocabulary for combat (BartleBot #204): "profile" means a launch profile, and only that.

A Bot Combat Strategy was called a "combat profile" on some surfaces and in most of the code,
while "profile" meant a launch profile everywhere else. This keeps the word out of the combat
surfaces core holds, as BartleBot's ``test_vocabulary`` does out of its own.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

PACKAGE = Path(__file__).resolve().parents[1] / "bartleby"

COMBAT_MODULES = sorted(
    [
        *(PACKAGE / "runtime" / "combat").glob("*.py"),
        *(PACKAGE / "qt" / "combat").glob("*.py"),
        PACKAGE / "qt" / "widgets" / "cards" / "strategy_card.py",
        PACKAGE / "qt" / "widgets" / "pills" / "tag_pill.py",
    ]
)

ALLOWED = re.compile(r"launch profiles?", re.IGNORECASE)


def _strays(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if "profile" in ALLOWED.sub("", line).lower()]


@pytest.mark.parametrize("path", COMBAT_MODULES, ids=lambda p: p.relative_to(PACKAGE).as_posix())
def test_no_combat_module_says_profile(path: Path):
    """Strings, comments and names alike: a string literal is what the user reads, and
    the rest is what the next string is written from."""
    assert _strays(path.read_text(encoding="utf-8")) == []


def test_the_scan_would_catch_one():
    assert _strays("label = \"In profile 'Solo' only\"") != []
    assert _strays("# fills the launch profile's row") == []
