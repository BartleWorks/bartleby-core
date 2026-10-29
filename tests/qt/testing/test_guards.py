"""The Qt guards: each finds what it is for on a made-up package, and core passes them all.

Core's Qt tier is read with these allowlists, each entry carrying its reason, as every
product's must.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from bartleby.qt.testing import guards

QT = Path(__file__).resolve().parents[3] / "bartleby" / "qt"

#: This module states the rules it checks, so its strings and docstrings name the very
#: selectors and size calls the rules forbid.
_RULE_TEXT = "testing/guards.py states the rules it checks, so it names what they forbid."

FONT_ALLOW = {"theming/theme.py": "Defines the tokens.", "testing/guards.py": _RULE_TEXT}
QSS_ALLOW = {
    "theming/theme.py": "Owns the global stylesheet, with the art to back it.",
    "testing/guards.py": _RULE_TEXT,
}


def _package(root: Path, files: dict[str, str]) -> Path:
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


# ---------------------------------------------------------------------------
# Each guard, on a package made for it
# ---------------------------------------------------------------------------


def test_a_widget_baking_a_theme_value_without_a_refresh_is_an_offender(tmp_path):
    root = _package(
        tmp_path / "pkg",
        {
            "panels.py": (
                "class Stale(QWidget):\n"
                "    def __init__(self):\n"
                "        self.setStyleSheet(f'color: {THEME.text};')\n"
                "class Mixed(ThemedMixin, QWidget):\n"
                "    def __init__(self):\n"
                "        self.refresh_theme_styles()\n"
                "    def apply_theme_styles(self):\n"
                "        self.setStyleSheet(f'color: {THEME.text};')\n"
                "class Sized(QWidget):\n"
                "    def __init__(self):\n"
                "        self.setStyleSheet(f'font-size: {THEME.font_small_px}px;')\n"
            ),
            "icons.py": (
                "def _style(w):\n"
                "    w.setIcon(svg_icon('gear'))\n"
                "class UsesHelper(QWidget):\n"
                "    def __init__(self):\n"
                "        _style(self)\n"
            ),
        },
    )

    found = guards.theme_refresh_offenders(root)

    assert [f.key for f in found.offenders] == ["icons.py:UsesHelper", "panels.py:Stale"]


def test_the_mixin_listed_second_or_never_applied_is_misuse(tmp_path):
    root = _package(
        tmp_path / "pkg",
        {
            "w.py": (
                "class Second(QWidget, ThemedMixin):\n"
                "    def __init__(self):\n"
                "        self.refresh_theme_styles()\n"
                "    def apply_theme_styles(self): pass\n"
                "class Never(ThemedMixin, QWidget):\n"
                "    def apply_theme_styles(self): pass\n"
            )
        },
    )

    problems = guards.mixin_misuse(root)

    assert len(problems) == 2
    assert problems[0].startswith("w.py:Second: ThemedMixin must come first")
    assert problems[1] == "w.py:Never: never calls self.refresh_theme_styles() outside changeEvent"


@pytest.mark.parametrize(
    "line",
    [
        "w.setStyleSheet('font-size: 11px')",
        "font.setPointSize(9)",
        "font.setPixelSize(12)",
        "QFont('Segoe UI', 9)",
        "size = font.pointSize()",
        "_FONT_PX = 11",
    ],
)
def test_a_literal_font_size_is_an_offender(tmp_path, line):
    root = _package(tmp_path / "pkg", {"a.py": f"# font.setPointSize(9) in a comment is prose\n{line}\n"})

    assert [f.key for f in guards.literal_font_sizes(root).offenders] == ["a.py:2"]


def test_a_bare_push_button_is_keyed_by_its_name_and_an_inline_one_by_its_line(tmp_path):
    root = _package(
        tmp_path / "pkg",
        {
            "bar.py": (
                "self._stop = QPushButton('Stop all')\n"
                "row.addWidget(QPushButton('Go'))\n"
                "icon = IconButton('gear', tooltip='Settings')\n"
                "sheet = 'QPushButton(not a call)'\n"
            )
        },
    )

    found = guards.bare_buttons(root, {"bar.py:_stop": "a stand-in"})

    assert [f.key for f in found.offenders] == ["bar.py:<line 2>"]


def test_a_second_primary_in_a_module_is_an_offender(tmp_path):
    root = _package(
        tmp_path / "pkg",
        {
            "two.py": "a = ActionButton('Save', style=ActionStyle.PRIMARY)\nb = ActionButton('Run', style=ActionStyle.PRIMARY)\n",
            "one.py": "a = ActionButton('Save', style=ActionStyle.PRIMARY)\n",
            "widgets/buttons/action_button.py": "if s is ActionStyle.PRIMARY: pass\nif s is ActionStyle.PRIMARY: pass\n",
        },
    )

    assert [f.key for f in guards.extra_primaries(root).offenders] == ["two.py"]


def test_qss_on_a_compound_widget_is_an_offender_and_a_comment_or_docstring_is_not(tmp_path):
    root = _package(
        tmp_path / "pkg",
        {
            "a.py": (
                '"""Never style QSpinBox: its steppers vanish."""\n'
                "w.setStyleSheet('QSpinBox { border: 1px solid red; }')\n"
                "w.setStyleSheet('/* QScrollBar is theme.py\\'s */ QLabel { color: red; }')\n"
            )
        },
    )

    found = guards.compound_widget_qss(root)

    assert [(f.key, f.detail.split(":")[0]) for f in found.offenders] == [("a.py:2", "QSpinBox")]


def test_a_modal_in_a_coroutine_is_an_offender_and_a_deferral_is_not(tmp_path):
    root = _package(
        tmp_path / "pkg",
        {
            "a.py": (
                "async def f(self):\n"
                "    QMessageBox.critical(self, 't', 'x')\n"
                "    menu.exec(point)\n"
                "    QTimer.singleShot(0, lambda: QMessageBox.critical(self, 't', 'x'))\n"
                "def g(self):\n"
                "    dialog.exec()\n"
            )
        },
    )

    found = guards.modals_in_coroutines(root)

    assert sorted(f.detail for f in found.offenders) == ["QMessageBox.critical", "menu.exec"]


# ---------------------------------------------------------------------------
# Core's own Qt tier
# ---------------------------------------------------------------------------


def _assert_clean(result) -> None:
    assert not result, result.message()
    assert not result.stale, f"stale allowlist entries: {result.stale}"


def test_every_themed_widget_in_core_refreshes_on_a_theme_change():
    _assert_clean(guards.theme_refresh_offenders(QT))


def test_every_mixin_user_in_core_uses_it_correctly():
    assert guards.mixin_misuse(QT) == []


def test_no_literal_font_size_in_core():
    _assert_clean(guards.literal_font_sizes(QT, FONT_ALLOW))


def test_no_bare_push_button_in_core():
    _assert_clean(guards.bare_buttons(QT))


def test_at_most_one_primary_per_module_in_core():
    _assert_clean(guards.extra_primaries(QT))


def test_no_qss_in_core_targets_a_compound_widget():
    _assert_clean(guards.compound_widget_qss(QT, QSS_ALLOW))


def test_no_coroutine_in_core_opens_a_modal_itself():
    _assert_clean(guards.modals_in_coroutines(QT.parent))
