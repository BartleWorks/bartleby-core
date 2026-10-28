"""Render sheets: a sheet draws in both themes, two runs agree, and a real change is named."""

from __future__ import annotations

import os

from PyQt6.QtWidgets import QHBoxLayout, QLabel, QWidget

from bartleby.qt.testing.render_sheet import Surface, compare, core_themes, is_blank, render
from bartleby.qt.theming.theme import DARK, LIGHT, active_theme
from bartleby.qt.widgets.buttons.action_button import ActionButton, ActionStyle
from bartleby.qt.widgets.buttons.icon_button import IconButton
from bartleby.qt.widgets.labels.themed_label import ThemedTextLabel


def _buttons() -> QWidget:
    row = QWidget()
    layout = QHBoxLayout(row)
    for style in ActionStyle:
        layout.addWidget(ActionButton("play", style.name.title(), style=style))
    layout.addWidget(IconButton("gear", tooltip="Settings"))
    return row


#: Core's own sheet: the button vocabulary and a themed label.
SHEET = [
    Surface("buttons", _buttons, (520, 48)),
    Surface("label", lambda: ThemedTextLabel("P1 · Player"), (160, 32)),
]


def test_a_sheet_is_drawn_once_per_theme(qapp, tmp_path):
    written = render(SHEET, tmp_path)

    assert sorted(p.name for p in written) == [
        "buttons.dark.png",
        "buttons.light.png",
        "label.dark.png",
        "label.light.png",
    ]
    assert not any(is_blank(p) for p in written)


def test_two_runs_on_one_tree_compare_identical(qapp, tmp_path):
    render(SHEET, tmp_path / "a")
    render(SHEET, tmp_path / "b")

    assert compare(tmp_path / "a", tmp_path / "b") == []


def test_a_surface_that_changed_is_named_and_one_only_one_run_has_is_too(qapp, tmp_path):
    render(SHEET, tmp_path / "a")
    changed = [Surface("buttons", lambda: QLabel("not the buttons"), (520, 48)), SHEET[1]]
    render(changed, tmp_path / "b")
    (tmp_path / "b" / "label.light.png").unlink()

    assert compare(tmp_path / "a", tmp_path / "b") == ["buttons.dark", "buttons.light", "label.light"]


def test_the_themes_differ_and_the_theme_in_force_is_put_back(qapp, tmp_path):
    before = active_theme()

    render(SHEET, tmp_path)

    assert active_theme() is before
    assert compare(tmp_path / "x", tmp_path / "x") == []
    assert set(core_themes().themes.values()) == {LIGHT, DARK}
    assert (tmp_path / "buttons.light.png").read_bytes() != (tmp_path / "buttons.dark.png").read_bytes()


def test_a_surface_of_one_color_is_blank(qapp, tmp_path):
    render([Surface("empty", QWidget, (40, 40))], tmp_path)

    assert is_blank(tmp_path / "empty.light.png")


def test_the_offscreen_platform_is_pointed_at_the_systems_fonts(monkeypatch, tmp_path):
    from bartleby.qt.testing.render_sheet import use_offscreen_platform  # noqa: PLC0415

    monkeypatch.delenv("QT_QPA_PLATFORM", raising=False)
    monkeypatch.delenv("QT_QPA_FONTDIR", raising=False)
    monkeypatch.setenv("WINDIR", str(tmp_path))

    use_offscreen_platform()

    assert os.environ["QT_QPA_PLATFORM"] == "offscreen"
    assert os.environ["QT_QPA_FONTDIR"] == str(tmp_path / "Fonts")
