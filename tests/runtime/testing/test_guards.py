"""The guards' shared reporting, the game key guard, and both run over core itself."""

from __future__ import annotations

from pathlib import Path

from bartleby.runtime.testing.guards import Finding, fixed_key_presses, report

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
