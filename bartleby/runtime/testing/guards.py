"""Static guards: conventions a test holds, run by every product over its own package.

A rule kept only in a document erodes, and one nobody tests is missing from half the code:
BartleBot's theme guard found the refresh its CLAUDE.md documented missing from 22 of its 43
themed classes. So each family convention that can be read from the source is a function
here, taking the package to read and an allowlist, and each product's suite calls it over
its own package. Core runs them over ``bartleby/``.

An allowlist maps a key to the reason it is exempt, and the reason is the deliverable: it is
printed when the guard fails, so the next person meets a decision rather than a judgment
call. A key names one finding (``"gui/bar_a.py:stop_button"``), or a whole module
(``"gui/bar_a.py"``), which is how a product lists a stand-in a later stage deletes. A key
that matches nothing is *stale*, and a test should fail on it too: an exemption for
something that is gone silences the check for that name if it ever comes back.

This module holds what every guard shares and the guards about the client runtime. The Qt
guards are in ``bartleby.qt.testing.guards``.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Finding:
    """One place a convention is broken."""

    #: What an allowlist names to exempt it: usually ``"<module>:<name>"``.
    key: str
    #: The module, as a posix path relative to the package root the guard read.
    module: str
    line: int
    #: What is wrong there, in a few words.
    detail: str

    def __str__(self) -> str:
        return f"{self.key}  ({self.module}:{self.line})  {self.detail}"


@dataclass(frozen=True)
class Report:
    """What a guard found, less what its allowlist exempts."""

    offenders: tuple[Finding, ...]
    #: Allowlist keys that match no finding.
    stale: tuple[str, ...]
    #: How to fix an offender, printed with them.
    advice: str = ""

    def __bool__(self) -> bool:
        return bool(self.offenders)

    def message(self) -> str:
        """The offenders and the advice, for an assertion."""
        lines = [f"{len(self.offenders)} offender(s):", *(f"  {f}" for f in self.offenders)]
        if self.advice:
            lines += ["", self.advice]
        return "\n".join(lines)


def report(findings: Iterable[Finding], allow: Mapping[str, str], advice: str = "") -> Report:
    """Split *findings* into offenders and allowed ones, and name the stale allowlist keys.

    A finding is allowed when *allow* names its key or its module.
    """
    found = list(findings)
    offenders = tuple(f for f in found if f.key not in allow and f.module not in allow)
    used = {f.key for f in found} | {f.module for f in found}
    stale = tuple(sorted(k for k in allow if k not in used))
    return Report(offenders, stale, advice)


def python_files(package_root: Path) -> list[Path]:
    """Every module under *package_root*, sorted, less ``__pycache__``."""
    return sorted(p for p in Path(package_root).rglob("*.py") if "__pycache__" not in p.parts)


def module_name(path: Path, package_root: Path) -> str:
    return path.relative_to(package_root).as_posix()


def parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Game keys
# ---------------------------------------------------------------------------

#: Keys that are not an action the player can rebind: Space advances dialogue whatever jump is
#: on, and Escape closes a page as CloseUI, which the game does not let a player rebind.
FIXED_KEYS = frozenset({"ESC", "SPACEBAR"})

_KEYCODE = re.compile(r"\bKeycode\.(\w+)")


def fixed_key_presses(package_root: Path, allow: Mapping[str, str] | None = None) -> Report:
    """Every game action is pressed through ``game_keys.press``, never as a fixed letter.

    A ``Keycode`` named in a product presses that key whatever the account has it bound to,
    and a player can rebind an action, so X may then do something else
    (``docs/KEY_BINDINGS.md``). Only Space and Escape stay ``Keycode``\\ s, being no
    rebindable action. Keyed ``"<module>:<line>"``.
    """
    findings = []
    for path in python_files(package_root):
        module = module_name(path, package_root)
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for name in _KEYCODE.findall(line):
                if name not in FIXED_KEYS:
                    findings.append(Finding(f"{module}:{number}", module, number, f"Keycode.{name}"))
    return report(findings, allow or {}, "Press these with game_keys.press(client, GameAction...).")
