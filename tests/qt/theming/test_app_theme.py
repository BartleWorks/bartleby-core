"""The theme setting, applied to the whole app."""

from __future__ import annotations

import pytest

from bartleby.qt.theming import theme
from bartleby.qt.theming.app_theme import apply_app_theme


@pytest.fixture(autouse=True)
def _restore_theme():
    before = theme._current_theme
    yield
    theme.set_theme_instance(before)


@pytest.mark.parametrize(("mode", "expected"), [("light", theme.LIGHT), ("dark", theme.DARK)])
def test_a_mode_is_that_theme(qapp, mode, expected):
    apply_app_theme(qapp, mode)

    assert theme.THEME.accent == expected.accent
    assert theme.THEME.bg == expected.bg


@pytest.mark.parametrize(("mode", "accent"), [("light", "#ff8800"), ("dark", "#ffaa44")])
def test_a_products_accent_replaces_the_themes_own(qapp, mode, accent):
    apply_app_theme(qapp, mode, accents=("#ff8800", "#ffaa44"))

    assert theme.THEME.accent == accent
    assert theme.THEME.bg == theme.resolve_theme(mode).bg


def test_a_custom_theme_keeps_the_colors_the_user_chose(qapp):
    apply_app_theme(qapp, "custom", {"accent": "#123456"}, accents=("#ff8800", "#ffaa44"))

    assert theme.THEME.accent == "#123456"


def test_the_app_gets_the_themes_palette_and_stylesheet(qapp):
    apply_app_theme(qapp, "dark")

    assert qapp.styleSheet() == theme.THEME.global_stylesheet()
    assert qapp.palette().window().color().name() == theme.THEME.build_palette().window().color().name()
