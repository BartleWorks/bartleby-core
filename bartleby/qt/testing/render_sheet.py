"""Render sheets: every surface a product draws, rendered offscreen in each theme and compared.

A render sheet is a product's list of :class:`Surface`\\ s, each built from a factory with
sample data at a fixed size. :func:`render` draws every one in every theme and writes a PNG
per surface and theme; :func:`compare` names every surface whose pixels differ between two
such folders. Rendered on ``master`` before a stage starts and on the stage's branch before it
merges, the comparison is the visual gate: a surface that moved into core is pixel-identical
in the product it came from, unless the stage names the change. A design system's component
sheet is the model.

Sample data only, ever: accounts named ``P1`` to ``P4``, no wizard names, no game art. A
sheet is shared in reviews and pasted into issues.

Each product runs its own sheet with ``tools/render_sheet.py``, under the offscreen platform,
so the pixels depend on neither the screen's scaling nor a window manager. A product whose
widgets still read a theme of their own (BartleBot's, until it adopts core's) passes its own
:class:`Themes`.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

from PyQt6.QtCore import QEvent, Qt
from PyQt6.QtGui import QImage
from PyQt6.QtWidgets import QApplication, QWidget

#: Turns of the event loop between showing a surface and grabbing it: enough for layouts,
#: deferred polish and a ``QTimer.singleShot(0, ...)`` restyle to land.
SETTLE_TURNS = 5


@dataclass(frozen=True)
class Surface:
    """One thing a product draws, built the same way every time."""

    #: The PNG's stem, so a name a later stage keeps stays comparable: ``"players_tab"``.
    name: str
    #: Builds the widget with sample data. Called once per theme, with that theme current.
    build: Callable[[], QWidget]
    #: Width and height, in logical pixels.
    size: tuple[int, int]


@dataclass(frozen=True)
class Themes:
    """The themes a sheet is drawn in, and how to make one current."""

    #: Name to theme instance, in the order they are drawn: ``{"light": LIGHT, "dark": DARK}``.
    themes: Mapping[str, object]
    #: The theme instance in force, so :func:`render` can put it back.
    active: Callable[[], object]
    #: Makes an instance current, as ``set_theme_instance`` does.
    set_active: Callable[[object], None]


def core_themes() -> Themes:
    """Core's light and dark themes."""
    from bartleby.qt.theming import theme  # noqa: PLC0415

    return Themes({"light": theme.LIGHT, "dark": theme.DARK}, theme.active_theme, theme.set_theme_instance)


def apply_theme(app: QApplication, theme: object) -> None:
    """Push *theme*'s palette, font and stylesheet to the whole app, as the products do at startup."""
    app.setPalette(theme.build_palette())
    app.setFont(theme.ui_font())
    app.setStyleSheet(theme.global_stylesheet())


def _settle(app: QApplication) -> None:
    for _ in range(SETTLE_TURNS):
        app.processEvents()


def _free(app: QApplication, widget: QWidget) -> None:
    widget.close()
    widget.deleteLater()
    app.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def render_one(surface: Surface) -> QImage:
    """*surface* drawn in the theme now current, at its size."""
    app = QApplication.instance()
    widget = surface.build()
    try:
        widget.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
        widget.resize(*surface.size)
        widget.show()
        _settle(app)
        return widget.grab().toImage().convertToFormat(QImage.Format.Format_ARGB32)
    finally:
        _free(app, widget)


def render(surfaces: Iterable[Surface], out_dir: Path, themes: Themes | None = None) -> list[Path]:
    """Draw every surface in every theme into *out_dir*, as ``<name>.<theme>.png``.

    Needs a ``QApplication``. The theme, palette, font and stylesheet in force before are put
    back after, so a test can render a sheet without leaking a theme into the next one.
    """
    themes = themes or core_themes()
    app = QApplication.instance()
    if app is None:
        raise RuntimeError("render() needs a QApplication")
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    surfaces = list(surfaces)
    before = (themes.active(), app.palette(), app.font(), app.styleSheet())
    written = []
    try:
        for theme_name, theme in themes.themes.items():
            themes.set_active(theme)
            apply_theme(app, theme)
            for surface in surfaces:
                path = out_dir / f"{surface.name}.{theme_name}.png"
                if not render_one(surface).save(str(path), "PNG"):
                    raise OSError(f"could not write {path}")
                written.append(path)
    finally:
        instance, palette, font, sheet = before
        themes.set_active(instance)
        app.setPalette(palette)
        app.setFont(font)
        app.setStyleSheet(sheet)
    return written


def _load(path: Path) -> QImage:
    return QImage(str(path)).convertToFormat(QImage.Format.Format_ARGB32)


def compare(before: Path, after: Path) -> list[str]:
    """Every image stem in either folder whose pixels differ, or which only one of them has."""
    stems = {p.stem for p in Path(before).glob("*.png")} | {p.stem for p in Path(after).glob("*.png")}
    changed = []
    for stem in sorted(stems):
        a, b = Path(before) / f"{stem}.png", Path(after) / f"{stem}.png"
        if not (a.exists() and b.exists()) or _load(a) != _load(b):
            changed.append(stem)
    return changed


def is_blank(path: Path) -> bool:
    """True when every pixel of the image is the same color: a surface that drew nothing."""
    image = _load(path)
    if image.isNull() or image.width() == 0 or image.height() == 0:
        return True
    data = image.constBits().asstring(image.sizeInBytes())
    first = data[:4]
    return data == first * (len(data) // 4)
