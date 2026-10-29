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

This module holds what every guard shares, the guards about the client runtime, and the
copy check every product runs against core. The Qt guards are in
``bartleby.qt.testing.guards``.
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


# ---------------------------------------------------------------------------
# Copies of core
# ---------------------------------------------------------------------------

#: Core's own package, which :func:`core_copies` compares a product against.
CORE_PACKAGE = Path(__file__).resolve().parents[2]


def _code(tree: ast.Module) -> str | None:
    """*tree* with its imports and docstrings left out, as a string to compare; None if nothing is left.

    A module moved into core keeps its body and changes its imports, and the paths its
    docstrings name, so neither tells a copy from the original.
    """

    class _Strip(ast.NodeTransformer):
        def visit_Import(self, node):  # noqa: N802 - ast's name
            return None

        visit_ImportFrom = visit_Import  # noqa: N815 - ast's name

        def generic_visit(self, node):
            body = getattr(node, "body", None)
            if (
                isinstance(body, list)
                and body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                node.body = body[1:] or [ast.Pass()]
            return super().generic_visit(node)

    stripped = _Strip().visit(tree)
    if all(isinstance(n, ast.Pass) for n in stripped.body):
        return None
    return ast.dump(stripped, annotate_fields=False)


def core_copies(package_root: Path, allow: Mapping[str, str] | None = None, core_root: Path = CORE_PACKAGE) -> Report:
    """No module of a product is a copy of one of core's.

    Every shared module exists once, in core, and a product imports it from there
    (``docs/ARCHITECTURE.md``). A copy is a module with the file name of one of core's and,
    imports and docstrings aside, the same code: what a module left behind when it moved
    looks like, and what drifts from the original the moment either is fixed. An
    ``__init__.py`` and a module of nothing but imports are never one. Keyed by the
    product's module path.
    """
    core: dict[str, list[tuple[str, str]]] = {}
    for path in python_files(core_root):
        if path.name != "__init__.py" and (code := _code(parse(path))) is not None:
            core.setdefault(path.name, []).append((module_name(path, core_root), code))
    findings = []
    for path in python_files(package_root):
        if path.name not in core:
            continue
        code = _code(parse(path))
        module = module_name(path, package_root)
        for original, theirs in core[path.name]:
            if code == theirs:
                findings.append(Finding(module, module, 1, f"a copy of core's bartleby/{original}"))
    return report(findings, allow or {}, "Delete the copy and import core's module instead.")


# ---------------------------------------------------------------------------
# Product names and versions in shared code
# ---------------------------------------------------------------------------

#: A version written out: three dotted numbers standing alone, as ``"0.1.0"``.
_VERSION = re.compile(r"(?<![\w.])\d+\.\d+\.\d+(?![\w.])")


def _docstrings(tree: ast.Module) -> set[int]:
    """The ids of every docstring's constant: a module's, a class's or a function's, and an attribute's after it."""
    found = set()
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        for statement in body if isinstance(body, list) else ():
            if (
                isinstance(statement, ast.Expr)
                and isinstance(statement.value, ast.Constant)
                and isinstance(statement.value.value, str)
            ):
                found.add(id(statement.value))
    return found


def product_literals(package_root: Path, names: Iterable[str], allow: Mapping[str, str] | None = None) -> Report:
    """No string in shared code names a product or a version: both come from the ``Product``.

    A name or a version written out in core is right in one product and wrong in every
    other, and drifts from the one it was right in (``bartleby.shared.product``). Matched
    in any case, so ``"bartlebot."`` is caught as well as ``"BartleBot"``. Docstrings and
    comments are left alone: they say where a module came from and whose bug it fixed,
    which is history, not a value. Keyed ``"<module>:<line>"``.
    """
    pattern = re.compile("|".join(re.escape(n) for n in names), re.IGNORECASE)
    findings = []
    for path in python_files(package_root):
        module = module_name(path, package_root)
        tree = parse(path)
        docstrings = _docstrings(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Constant) or not isinstance(node.value, str) or id(node) in docstrings:
                continue
            if (match := pattern.search(node.value) or _VERSION.search(node.value)) is not None:
                findings.append(Finding(f"{module}:{node.lineno}", module, node.lineno, f"names {match.group()!r}"))
    return report(findings, allow or {}, "Take the name or the version from bartleby.shared.product's Product.")
