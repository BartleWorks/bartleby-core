"""The guards' shared reporting, the game key guard, and both run over core itself."""

from __future__ import annotations

from pathlib import Path

from bartleby.runtime.testing.guards import (
    CORE_PACKAGE,
    Finding,
    core_copies,
    fixed_key_presses,
    product_literals,
    report,
)

PACKAGE = Path(__file__).resolve().parents[3] / "bartleby"


def _package(root: Path, files: dict[str, str]) -> Path:
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


def test_an_allowlist_key_exempts_one_finding_or_a_whole_module():
    found = [Finding("a.py:x", "a.py", 1, ""), Finding("a.py:y", "a.py", 2, ""), Finding("b.py:z", "b.py", 3, "")]

    assert [f.key for f in report(found, {"a.py:x": "why"}).offenders] == ["a.py:y", "b.py:z"]
    assert [f.key for f in report(found, {"a.py": "a stand-in"}).offenders] == ["b.py:z"]


def test_an_allowlist_key_that_matches_nothing_is_stale():
    found = [Finding("a.py:x", "a.py", 1, "")]

    assert report(found, {"a.py:x": "why", "gone.py": "deleted", "a.py:old": "renamed"}).stale == (
        "a.py:old",
        "gone.py",
    )


def test_a_report_with_offenders_is_true_and_says_how_to_fix_them():
    result = report([Finding("a.py:x", "a.py", 1, "bad")], {}, "Do it the other way.")

    assert result
    assert "a.py:x" in result.message() and "Do it the other way." in result.message()
    assert not report([], {})


def test_a_fixed_letter_is_found_and_space_and_escape_are_not(tmp_path):
    root = _package(
        tmp_path / "pkg",
        {
            "keys.py": "send(Keycode.X)\nsend(Keycode.SPACEBAR)\nsend(Keycode.ESC)\n",
            "sub/more.py": "\n\nawait client.send_key(Keycode.B, 0.1)\n",
        },
    )

    assert [f.key for f in fixed_key_presses(root).offenders] == ["keys.py:1", "sub/more.py:3"]


def test_no_game_action_in_core_is_pressed_by_a_fixed_key():
    """A Keycode named in bartleby/ presses that key whatever the account has it bound to."""
    result = fixed_key_presses(PACKAGE)

    assert not result, result.message()


_ORIGINAL = '''"""Core's version of the module."""

from bartleby.shared import thing


def area(width, height):
    """Width times height."""
    return width * height
'''


def test_a_module_with_core_s_name_and_code_is_a_copy_whatever_its_imports_and_docstrings(tmp_path):
    core = _package(tmp_path / "core", {"runtime/geometry.py": _ORIGINAL, "runtime/__init__.py": ""})
    copy = _ORIGINAL.replace("bartleby.shared", "bartlebot.shared").replace("Core's version", "BartleBot's version")
    product = _package(tmp_path / "product", {"game/geometry.py": copy})

    result = core_copies(product, core_root=core)

    assert [f.key for f in result.offenders] == ["game/geometry.py"]
    assert "bartleby/runtime/geometry.py" in result.offenders[0].detail


def test_a_module_whose_code_differs_is_no_copy(tmp_path):
    core = _package(tmp_path / "core", {"runtime/geometry.py": _ORIGINAL})
    changed = _ORIGINAL.replace("return width * height", "return width * height * 2")
    product = _package(tmp_path / "product", {"game/geometry.py": changed, "game/other.py": _ORIGINAL})

    assert not core_copies(product, core_root=core)


def test_an_init_or_a_module_of_imports_alone_is_never_a_copy(tmp_path):
    reexport = '"""Re-exports."""\n\nfrom bartleby.shared import thing  # noqa: F401\n'
    core = _package(tmp_path / "core", {"qt/__init__.py": "", "qt/names.py": reexport})
    product = _package(tmp_path / "product", {"gui/__init__.py": "", "gui/names.py": reexport})

    assert not core_copies(product, core_root=core)


def test_the_copy_check_reads_core_itself_by_default():
    assert CORE_PACKAGE == PACKAGE.resolve()
    assert core_copies(PACKAGE).offenders  # core against itself: every module is its own copy


_NAMES = ("BartleBot", "BartleBuddy")


def test_a_product_name_in_a_string_is_found_in_any_case(tmp_path):
    root = _package(
        tmp_path,
        {
            "shown.py": 'TEXT = "Launch it from BartleBot to embed it."\n',
            "prefix.py": 'OURS = ("bartlebuddy.", "bartleby.")\n',
        },
    )

    result = product_literals(root, _NAMES)

    assert sorted(f.key for f in result.offenders) == ["prefix.py:1", "shown.py:1"]


def test_a_version_in_a_string_is_found_but_a_longer_dotted_number_is_not(tmp_path):
    root = _package(tmp_path, {"m.py": 'VERSION = "0.1.0"\nHOST = "127.0.0.1"\nSCALE = "1.5"\n'})

    result = product_literals(root, _NAMES)

    assert [f.key for f in result.offenders] == ["m.py:1"]


def test_docstrings_and_comments_may_name_a_product(tmp_path):
    """They are history: where a module came from, whose bug it fixed."""
    source = '''"""Moved from BartleBot's bartlebot/gui/ui_state.py."""

# BartleBuddy's runtime runs on its own thread.


class Tile:
    """Says what BartleBot said."""

    size: int = 0
    """Version 0.1.0 had none."""

    def show(self):
        """As BartleBuddy draws it."""
'''
    root = _package(tmp_path, {"m.py": source})

    assert not product_literals(root, _NAMES)
