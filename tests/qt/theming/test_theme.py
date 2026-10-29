"""Tests for bartleby.qt.theming.theme: a saved custom theme across a token rename, and the font tokens."""

from __future__ import annotations

import pytest

from bartleby.qt.theming.theme import LIGHT, RENAMED_TOKENS, build_custom_theme, saved_color


def test_a_color_saved_under_a_renamed_tokens_old_name_is_still_read():
    """#204 renamed ``pill_profile_bg``; a custom theme saved before keeps its color."""
    theme = build_custom_theme({"pill_profile_bg": "#123456"}, base=LIGHT)

    assert theme.pill_this_bot_bg == "#123456"


def test_the_new_name_wins_over_the_old():
    theme = build_custom_theme({"pill_profile_bg": "#123456", "pill_this_bot_bg": "#abcdef"}, base=LIGHT)

    assert theme.pill_this_bot_bg == "#abcdef"


def test_with_neither_name_saved_the_base_answers():
    assert saved_color({}, "pill_this_bot_bg", LIGHT.pill_this_bot_bg) == LIGHT.pill_this_bot_bg
    assert saved_color({"accent": "#000000"}, "accent", "#ffffff") == "#000000"


def test_every_renamed_token_is_a_real_one():
    assert all(hasattr(LIGHT, new) and not hasattr(LIGHT, old) for new, old in RENAMED_TOKENS.items())


def test_the_derived_sizes_stay_in_their_order() -> None:
    """The four UI sizes are one ladder, so a body size change keeps their order."""
    from bartleby.qt.theming.theme import LIGHT

    assert LIGHT.font_micro_px < LIGHT.font_subtext_px < LIGHT.font_small_px
    assert LIGHT.font_small_px < LIGHT.font_body_px < LIGHT.font_heading_px
    assert LIGHT.font_node_sub_px < LIGHT.font_node_px


def test_no_font_token_varies_by_theme() -> None:
    """Sizes are the same in every theme, which is what lets a widget bake one in.

    ``theme_refresh_offenders`` (``bartleby.qt.testing.guards``) demands that a widget re-derive any ``THEME`` value it
    writes into its own stylesheet, because a color set under one theme is stale under
    the next. It excuses the font tokens on the strength of this: a size is the same
    whichever theme is active, so there is nothing to go stale. Give a theme a font size
    of its own and that exemption turns into a bug this test fails first.
    """
    from bartleby.qt.theming.theme import DARK, LIGHT, build_custom_theme

    sizes = lambda t: {  # noqa: E731
        name: getattr(t, name) for name in dir(t) if name.startswith("font_") and isinstance(getattr(t, name), int)
    }
    assert sizes(LIGHT) == sizes(DARK)
    assert sizes(build_custom_theme({"accent": "#ff0000"})) == sizes(LIGHT)


def test_the_font_helpers_size_in_pixels() -> None:
    """Not points: a font sized in points reports -1 for its pixel size, and every
    widget that measures its own text would measure the wrong thing."""
    from bartleby.qt.theming.theme import THEME

    assert THEME.ui_font().pixelSize() == THEME.font_body_px
    assert THEME.ui_font(THEME.font_micro_px, bold=True).pixelSize() == THEME.font_micro_px
    assert THEME.ui_font(bold=True).bold()
    assert THEME.font_mono().pixelSize() == THEME.font_body_px
    assert THEME.font_node_main().pixelSize() == THEME.font_node_px
    assert THEME.font_node_sub().pixelSize() == THEME.font_node_sub_px


def test_a_scaled_font_tracks_the_scale_it_is_given() -> None:
    """Scene text is drawn at a fractional scale, which whole pixels cannot express.

    Qt converts points at 96 logical DPI whatever the display scaling, so 0.75pt is
    one pixel and the px token the caller passes is the size at scale 1.
    """
    from bartleby.qt.theming.theme import THEME

    assert THEME.scaled_ui_font(12, 1.0).pointSizeF() == pytest.approx(9.0)
    assert THEME.scaled_ui_font(12, 2.0).pointSizeF() == pytest.approx(18.0)
    # Never zero or negative, however far the canvas is zoomed out.
    assert THEME.scaled_ui_font(12, 0.0001).pointSizeF() >= 1.0
