"""Apply the theme setting to the whole app: the palette, the font and the global stylesheet.

BartleBot's ``AppController._apply_theme`` and BartleBuddy's startup ``apply_theme`` were
two versions of this, and BartleBuddy's ran once, so Settings had nothing to call on a
switch. Moved here in Stage 11 of #167. Each product calls it at startup and again when
Settings closes with OK; a widget that baked a theme value in follows through its own
``changeEvent`` (``bartleby/qt/CLAUDE.md``, "GUI theming").
"""

from __future__ import annotations

from PyQt6.QtWidgets import QApplication

from bartleby.qt.theming.theme import DARK, THEME, build_custom_theme, resolve_theme, set_theme, set_theme_instance


def apply_app_theme(
    app: QApplication,
    mode: str,
    custom: dict[str, str] | None = None,
    *,
    accents: tuple[str, str] | None = None,
) -> None:
    """Make *mode*'s theme the app's: ``system``, ``light``, ``dark`` or ``custom``.

    *custom* is the colors a custom theme overrides, by token name. *accents* is a product's
    accent for the light and the dark theme, where it is not the theme's own: BartleBuddy's
    orange. A custom theme keeps the accent its colors say, since the user chose those.
    """
    if mode == "custom":
        set_theme_instance(build_custom_theme(custom or {}))
    elif accents is not None:
        base = resolve_theme(mode)
        set_theme_instance(build_custom_theme({"accent": accents[1] if base is DARK else accents[0]}, base))
    else:
        set_theme(mode)
    app.setPalette(THEME.build_palette())
    # The app font, so every widget that does not set its own size is the body
    # size in px rather than the platform's own 9pt. Passed without a class name,
    # which is what clears the per-class fonts Windows gives menus and tooltips.
    app.setFont(THEME.ui_font())
    app.setStyleSheet(THEME.global_stylesheet())
