"""The family's pills: the shape every product's small captions share, and the metrics behind it.

Moved from BartleBot's ``gui/widgets/pills/tag_pill.py``, which keeps the pills that
name a bot tag (``TagPill``, ``TagChip``, ``SuggestedTagPill``, ``KeywordChip``,
``ThisBotPill``) and the tag palette they share. A tag is colored by its group
everywhere it appears, so color on a pill means "tag group" in BartleBot, and the pills
here are the ones that claim no tag color, or claim another palette's.

:class:`NeutralPill` is a caption that wants to look like a pill without claiming a
color. :class:`ActionPill` is that one with a click behind it -- same metrics, so a pill
that leads somewhere and a pill that only names something are not two different shapes.
:class:`NeutralChip` is the gray chip with a ✕, for a list of plain strings the user
assembled. :class:`WarningPill` is the neutral one filled with ``warning``.

:class:`AccentPill` is a third: the same metrics again, outlined in the accent, for a
badge that has to be told apart from the pills it shares a row with.

:class:`AccountPill` is the other odd one: it claims a color, but from the account
palette rather than the tag one, and it wears it as an outline instead of a fill.
See its docstring for why the two color systems can share a screen without reading
as one.
"""

from __future__ import annotations

from PyQt6.QtCore import QEvent, QRectF, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFontMetrics, QPainter, QPen
from PyQt6.QtWidgets import QLabel, QPushButton, QSizePolicy, QWidget

from bartleby.qt.theming.account_accent import account_accent, account_tint
from bartleby.qt.theming.theme import THEME
from bartleby.qt.theming.themed import set_style_sheet

#: The inert pill's own metrics, shared with :class:`ActionPill` so a clickable pill and
#: a descriptive one are the same shape -- which is the whole reason the clickable one
#: exists rather than a chip.
_PILL_PAD_X = 6
_PILL_PAD_Y = 1  # the vertical half of pill_style's "1px 6px"


def pill_height() -> int:
    """How tall a pill is: its own text, plus the padding above and below.

    Measured rather than fixed, because the two pills have to agree and only one of
    them is drawn by this module: :class:`NeutralPill` is a ``QLabel``, whose size hint
    is its text, while :class:`ActionPill` states a hint of its own to keep a
    ``QPushButton``'s 80px platform minimum out of it. A constant held them together at
    one font size and split them at the next.
    """
    return QFontMetrics(THEME.ui_font(THEME.font_micro_px, bold=True)).height() + 2 * _PILL_PAD_Y


def pill_style(bg: str, fg: str, *, font_size: int | None = None, radius: int = 7, pad: str = "1px 6px") -> str:
    """The shared pill look, so a caller varies only what it means to vary."""
    font_size = THEME.font_micro_px if font_size is None else font_size
    return (
        f"background: {bg}; color: {fg}; font-size: {font_size}px;"
        f" font-weight: bold; border-radius: {radius}px; padding: {pad};"
    )


def paint_pill_outline(painter: QPainter, rect: QRectF, color: str) -> None:
    """Outline *rect* as a pill, with every edge the same whole number of device pixels.

    Worked in device pixels: *rect* is mapped through the painter's transform and its
    edges rounded onto the pixel grid before the pen is inset by half its width, so each
    edge covers whole rows and columns. At fractional scaling a widget's logical origin
    lands between device pixels (y=109 at 125% is 136.25), so snapping in logical
    coordinates would not be enough. The width is the scale rounded -- one pixel at 100%
    and 125%, two at 150% and up -- so the line keeps its weight as the display scales.
    """
    to_device = painter.deviceTransform()
    device = to_device.mapRect(rect)
    scale = to_device.m22() or 1.0
    width = max(1, round(scale))
    snapped = QRectF(
        round(device.left()),
        round(device.top()),
        round(device.right()) - round(device.left()),
        round(device.bottom()) - round(device.top()),
    )
    half = width / 2
    # Back into the painter's own coordinates rather than resetting its transform: a
    # reset clears the world transform but not the device pixel ratio's scale, so device
    # coordinates drawn after one are scaled a second time.
    to_logical, _invertible = to_device.inverted()
    box = to_logical.mapRect(snapped.adjusted(half, half, -half, -half))
    radius = box.height() / 2
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor(color))
    pen.setWidthF(width / scale)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawRoundedRect(box, radius, radius)
    painter.restore()


class NeutralChip(QPushButton):
    """A gray chip that removes the word it names when clicked.

    BartleBot's ``TagChip``'s shape without a tag's color, for a list of plain strings the
    user assembled: the quicksell keep list's keywords are the case. Color on a chip
    means "tag group" everywhere else in the app, so a keyword that borrowed one
    would read as a tag.

    The keep list is why this is a chip at all rather than the comma-separated line
    edit it replaced. At a 400px sidebar that field showed the *middle* of a scrolled
    string -- "electable Hooves, Skyscream, Frostbit, ..." -- which is neither
    readable nor safely editable, and removing one keyword from it meant selecting
    text between two commas.
    """

    removed = pyqtSignal(str)

    def __init__(self, text: str, *, tooltip: str | None = None, parent: QWidget | None = None) -> None:
        super().__init__(f"{text}  ✕", parent)
        self._text = text
        self._applying_style = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(tooltip or f"Remove {text}")
        self._restyle()
        self.clicked.connect(lambda: self.removed.emit(self._text))

    @property
    def value(self) -> str:
        return self._text

    def _restyle(self) -> None:
        if self._applying_style:  # see NeutralPill._restyle
            return
        self._applying_style = True
        try:
            base = pill_style(
                THEME.pill_neutral_bg, THEME.pill_text, font_size=THEME.font_subtext_px, radius=9, pad="2px 9px"
            )
            set_style_sheet(
                self,
                f"QPushButton {{ border: none; {base} }}QPushButton:hover {{ background: {THEME.edge_selected}; }}",
            )
        finally:
            self._applying_style = False

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.StyleChange:
            self._restyle()


class ActionPill(QPushButton):
    """A gray pill that runs a verb when clicked, and wears no ✕.

    :class:`NeutralPill`'s exact shape and color with a hit target behind it, so a row
    of these and a row of inert pills read as the same kind of thing. The Combat tab's
    Bot Default Strategy card is the case: it condenses every fight that has no override
    of its own into one card, one pill each, and a click on a pill opens the strategy
    picker for that fight — the click that used to land on the fight's own condensed row.

    No ✕, unlike :class:`NeutralChip`. A cross means "remove the thing this names", which
    for a fight would be an offer to delete a node out of the bot; the pill *leads* to
    the fight instead of destroying it.

    Carries a *key* beside its text — a state id, where the label is the fight's display
    name — because a card reuses its pills rather than rebuilding them, and an index into
    a list of labels is not a stable way to say which one was clicked.
    """

    activated = pyqtSignal(str)  # this pill's key

    def __init__(self, text: str = "", key: str = "", *, parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self._key = key
        self._applying_style = False
        self._flagged = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(pill_height())
        # Sized from the text rather than from the style. A ``QPushButton``'s own size
        # hint carries the platform's minimum button width -- 80px on Windows -- which
        # would make four pills wider than the sidebar they wrap inside.
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        # On the widget as well as in the stylesheet: a stylesheet ``font-size`` never
        # reaches ``QWidget.font()``, so :meth:`sizeHint` would measure at the base size
        # and leave every pill several pixels too wide.
        font = self.font()
        font.setPixelSize(THEME.font_micro_px)
        font.setBold(True)
        self.setFont(font)
        self._restyle()
        self.clicked.connect(lambda: self.activated.emit(self._key))

    @property
    def key(self) -> str:
        return self._key

    def set_pill(self, text: str, key: str) -> None:
        """Re-point this pill at another thing, keeping the widget."""
        self._key = key
        self.setText(text)
        self.updateGeometry()

    @property
    def flagged(self) -> bool:
        return self._flagged

    def set_flagged(self, flagged: bool) -> None:
        """Fill the pill with ``warning`` rather than gray: what it names wants a look.

        The Combat tab's case is a fight fled on the default this session (epic #201,
        Stage 7). The fill is the canvas's validation pill's, so the same color means
        the same thing; the click stays what it was.
        """
        if flagged != self._flagged:
            self._flagged = flagged
            self._restyle()

    def sizeHint(self) -> QSize:
        return QSize(QFontMetrics(self.font()).horizontalAdvance(self.text()) + 2 * _PILL_PAD_X, pill_height())

    def _restyle(self) -> None:
        if self._applying_style:  # see NeutralPill._restyle
            return
        self._applying_style = True
        try:
            bg, hover = (
                (THEME.warning, THEME.pill_warning_hover)
                if self._flagged
                else (THEME.pill_neutral_bg, THEME.edge_selected)
            )
            base = pill_style(bg, THEME.pill_text, font_size=THEME.font_micro_px, pad=f"0px {_PILL_PAD_X}px")
            set_style_sheet(
                self,
                f"QPushButton {{ border: none; {base} }}"
                f"QPushButton:hover:enabled {{ background: {hover}; }}"
                f"QPushButton:disabled {{ color: {THEME.text_subtle}; }}",
            )
        finally:
            self._applying_style = False

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.StyleChange:
            self._restyle()


class NeutralPill(QLabel):
    """A small, inert, gray pill for a caption that is not a tag.

    A bot card's version, a launch profile's resolution — descriptive text that wants
    the pill *shape* without claiming a tag color. Color on these cards means "tag
    group", and spending an accent on a version number made the loudest pill the one
    carrying the least information.

    ``pill_neutral_bg`` is a true gray on purpose: the blue-gray ``chip_neutral_bg``
    read as a third tag color beside the blue Rewards pill.

    Owns its own color, like BartleBot's ``TagPill``. The predecessor was a factory returning
    a bare ``QLabel`` for the caller to restyle from ``apply_theme_styles``, which put
    the staleness on every caller — see the themed-helper note in CLAUDE.md.
    """

    def __init__(self, text: str, parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self._applying_style = False
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedHeight(pill_height())
        self._restyle()

    def _restyle(self) -> None:
        # Guard is load-bearing: setStyleSheet delivers StyleChange straight back to
        # changeEvent, and the app dies in window construction with no traceback.
        if self._applying_style:
            return
        self._applying_style = True
        try:
            set_style_sheet(self, pill_style(THEME.pill_neutral_bg, THEME.pill_text))
        finally:
            self._applying_style = False

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.StyleChange:
            self._restyle()


class WarningPill(NeutralPill):
    """:class:`NeutralPill` filled with ``warning``: something here wants a look, and nothing is broken.

    The Combat tab's banner and default card wear one for fights fled on the default this
    session (epic #201, Stage 7). ``warning`` with ``pill_text`` is the canvas's validation
    pill, at 4.5:1 or better in both themes; ``error`` stays for what does not work.
    """

    def _restyle(self) -> None:
        if self._applying_style:  # see NeutralPill._restyle
            return
        self._applying_style = True
        try:
            set_style_sheet(self, pill_style(THEME.warning, THEME.pill_text))
        finally:
            self._applying_style = False


class AccentPill(QLabel):
    """An inert, accent-outlined pill marking what the row it leads is.

    :class:`NeutralPill`'s metrics with the accent's outline instead of a gray fill, for
    a badge that shares a row with pills naming *things*. The Combat tab's default
    strategy card is the case: its pill row is ``Default`` followed by a pill per fight,
    and the two have to be told apart at a glance because they are different kinds of
    word -- one qualifies the card, the rest are its contents.

    Outlined rather than filled, for :class:`AccountPill`'s reason turned around. There
    the outline keeps four saturated fills off one row; here it is that a *filled* accent
    pill beside gray ones reads as the loudest item in the list rather than as a label on
    it. It is also the affordance boundary on that card, where the fight pills are
    :class:`ActionPill`\\ s: no fill, no hover, no pointing hand, nothing to click.

    ``filled=True`` is the heavier rung, filled with the accent -- ``ActionButton``'s
    ``PRIMARY`` to the outline's ``SECONDARY``. The strategy cards' source pill uses both,
    so the three sources read as three weights: an Official one filled, a modified one
    outlined, a custom one gray.
    """

    def __init__(self, text: str, parent: QWidget | None = None, *, filled: bool = False) -> None:
        super().__init__(text, parent)
        self._applying_style = False
        self._filled = filled
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedHeight(pill_height())
        self._restyle()

    @property
    def filled(self) -> bool:
        return self._filled

    def _restyle(self) -> None:
        if self._applying_style:  # see NeutralPill._restyle
            return
        self._applying_style = True
        try:
            # One pixel of the padding goes to the border, so the text sits at the same
            # x inside this pill as inside the filled ones beside it. The filled form
            # keeps the border, in its own fill color, so both forms are the same size.
            bg, fg = (THEME.accent, THEME.pill_text) if self._filled else ("transparent", THEME.accent)
            set_style_sheet(
                self,
                f"background: {bg}; color: {fg};"
                f" font-size: {THEME.font_micro_px}px; font-weight: bold; border-radius: 7px;"
                f" border: 1px solid {THEME.accent}; padding: 0px {_PILL_PAD_X - 1}px;",
            )
        finally:
            self._applying_style = False

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.StyleChange:
            self._restyle()


class AccountPill(QLabel):
    """A pill naming one account, outlined in that account's color.

    Outlined rather than filled, and with ordinary text rather than colored text.
    Both follow from where it is used: a launch profile shows one of these per
    account, so four of them sit in a row, and four saturated fills make the row
    the loudest thing on a screen whose point is the launch profile's *name*. The color
    is a match target — you find the account card carrying the same hue — and an
    outline plus a low-alpha tint is enough to match on while leaving the name at
    full contrast, which colored text at 9px is not.

    That the shape differs from BartleBot's ``TagPill`` is the other half of it: tags mean
    something different by color, and the two palettes never have to be told apart
    because a filled pill and an outlined one are not the same object.

    Holds the palette *slot*, not a color — like BartleBot's ``TagPill`` holding its tag —
    so a theme switch re-resolves rather than repainting a stale hex.
    """

    def __init__(self, text: str, color_key: int, parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self._color_key = color_key
        self._applying_style = False
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedHeight(16)
        self._restyle()

    @property
    def color_key(self) -> int:
        return self._color_key

    def _restyle(self) -> None:
        if self._applying_style:  # see NeutralPill._restyle
            return
        self._applying_style = True
        try:
            set_style_sheet(
                self,
                f"background: {account_tint(self._color_key)}; color: {THEME.text};"
                f" font-size: {THEME.font_micro_px}px; font-weight: bold; border-radius: 8px;"
                f" border: 1px solid {account_accent(self._color_key)}; padding: 0px 6px;",
            )
        finally:
            self._applying_style = False

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.StyleChange:
            self._restyle()
