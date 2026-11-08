"""Tests for the app-wide "this control is disabled" signal.

Disabled state used to be carried entirely by ``QPalette.Disabled.ButtonText``
set to ``text_muted`` — the same color as *enabled* secondary text — on an
otherwise unchanged button, and by icons that Qt could not meaningfully gray out
because they had already been flattened to a single tint color.  The result was
readable either way, so "disabled" was indistinguishable from "enabled".
"""

from __future__ import annotations

import pytest
from PyQt6.QtGui import QColor, QIcon, QPalette

from bartleby.qt.theming.icon_utils import svg_icon, svg_pixmap
from bartleby.qt.theming.theme import DARK, LIGHT


@pytest.mark.parametrize("theme", [LIGHT, DARK], ids=["light", "dark"])
def test_disabled_text_differs_from_enabled_secondary_text(qapp, theme):
    """The whole bug: disabled text was literally text_muted, so it read as content."""
    assert theme.text_disabled.lower() != theme.text_muted.lower()
    assert theme.text_disabled.lower() != theme.text.lower()


@pytest.mark.parametrize("theme", [LIGHT, DARK], ids=["light", "dark"])
def test_disabled_fill_differs_from_the_enabled_button_fill(qapp, theme):
    """Text alone is too weak a signal — the fill has to move as well."""
    assert theme.surface_disabled.lower() != theme.surface.lower()


def _luminance(hex_color: str) -> float:
    """WCAG relative luminance."""
    color = QColor(hex_color)

    def channel(v: float) -> float:
        return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4

    return 0.2126 * channel(color.redF()) + 0.7152 * channel(color.greenF()) + 0.0722 * channel(color.blueF())


def _contrast(a: str, b: str) -> float:
    """WCAG contrast ratio, 1.0 (identical) to 21.0 (black on white)."""
    lo, hi = sorted((_luminance(a), _luminance(b)))
    return (hi + 0.05) / (lo + 0.05)


@pytest.mark.parametrize("theme", [LIGHT, DARK], ids=["light", "dark"])
def test_disabled_text_is_legible_as_off_but_not_as_content(theme):
    """Low contrast on purpose, in a band: under 3:1, the floor for large text, so it never
    reads as content, and over 2:1 so it can still be read. The light theme's was 1.8:1."""
    assert 2.0 <= _contrast(theme.text_disabled, theme.surface_disabled) < 3.0


@pytest.mark.parametrize("theme", [LIGHT, DARK], ids=["light", "dark"])
def test_disabled_fill_sits_measurably_apart_from_surface(theme):
    assert _contrast(theme.surface_disabled, theme.surface) >= 1.05


def test_dark_disabled_fill_sinks_toward_the_page():
    """Between ``bg`` and ``surface``: a disabled control sinks into the panel."""
    assert _luminance(DARK.bg) < _luminance(DARK.surface_disabled) < _luminance(DARK.surface)


def test_light_disabled_fill_is_grayed_well_clear_of_surface():
    """``surface`` is barely off white, so there is no room toward the page: the fill goes
    grayer instead, and needs more distance than the dark one to be seen. #e4e7ec, at
    1.10:1, did not read as disabled (#2)."""
    assert _luminance(LIGHT.surface_disabled) < _luminance(LIGHT.surface)
    assert _contrast(LIGHT.surface_disabled, LIGHT.surface) >= 1.15


@pytest.mark.parametrize("theme", [LIGHT, DARK], ids=["light", "dark"])
def test_palette_disabled_group_uses_the_disabled_tokens(qapp, theme):
    pal = theme.build_palette()
    disabled = QPalette.ColorGroup.Disabled

    assert pal.color(disabled, QPalette.ColorRole.ButtonText).name().lower() == theme.text_disabled.lower()
    assert pal.color(disabled, QPalette.ColorRole.Button).name().lower() == theme.surface_disabled.lower()


@pytest.mark.parametrize("theme", [LIGHT, DARK], ids=["light", "dark"])
def test_disabled_button_differs_from_enabled_button_in_both_roles(qapp, theme):
    """Guards the regression directly: both fill and text must change."""
    pal = theme.build_palette()
    active, disabled = QPalette.ColorGroup.Active, QPalette.ColorGroup.Disabled

    assert pal.color(active, QPalette.ColorRole.Button) != pal.color(disabled, QPalette.ColorRole.Button)
    assert pal.color(active, QPalette.ColorRole.ButtonText) != pal.color(disabled, QPalette.ColorRole.ButtonText)


def test_svg_icon_carries_an_explicit_disabled_pixmap(qapp):
    """Qt's generated disabled variant is a near no-op on a flat single-color icon."""
    icon = svg_icon("more_vert", 16)

    normal = icon.pixmap(16, 16, QIcon.Mode.Normal).toImage()
    disabled = icon.pixmap(16, 16, QIcon.Mode.Disabled).toImage()

    assert normal != disabled


def test_disabled_icon_is_actually_more_transparent(qapp):
    """Not just "different" — visibly faded."""
    icon = svg_icon("more_vert", 16)
    normal = icon.pixmap(16, 16, QIcon.Mode.Normal).toImage()
    disabled = icon.pixmap(16, 16, QIcon.Mode.Disabled).toImage()

    def total_alpha(img) -> int:
        return sum(img.pixelColor(x, y).alpha() for x in range(img.width()) for y in range(img.height()))

    assert total_alpha(disabled) < total_alpha(normal)


def test_svg_pixmap_opacity_defaults_to_opaque(qapp):
    """The fade is opt-in — existing svg_pixmap callers must be unaffected."""
    plain = svg_pixmap("more_vert", 16, tint="#ffffff").toImage()
    explicit = svg_pixmap("more_vert", 16, tint="#ffffff", opacity=1.0).toImage()

    assert plain == explicit
