"""A card that is one answer to a question: an icon, a name, and a line or two about it.

Moved from BartleBot's ``gui/dialogs/new_bot_dialog.py`` in Stage 11 of #167, where it is
New Bot's and the Templates dialog's gallery card; setup's shared pages draw their answers
with it. The cut: the Source pill BartleBot put beside a template's name is a pill the
product hands in (``pill``), since where a template comes from is BartleBot's to say.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFontMetrics, QIcon
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QRadioButton, QVBoxLayout, QWidget

from bartleby.qt.theming.icon_utils import svg_icon
from bartleby.qt.theming.theme import THEME
from bartleby.qt.theming.themed import ThemedMixin, set_style_sheet
from bartleby.qt.widgets.labels.art_icon_view import ArtIconView
from bartleby.qt.widgets.pickers.picker_popup import SECTION_ICON_PX

#: How far over its drawn size a glyph is rendered; see ``StartCard``.
_GLYPH_OVERSAMPLE = 4


class StartCard(ThemedMixin, QFrame):
    """One starting point in the gallery: an icon, a name, and a line or two about it.

    Selected by a click or by taking keyboard focus, so Tab walks the gallery and
    Enter, which is Next, takes the one it stopped on. A double-click is the click
    and Next together.

    Hover and selection are two looks, because they say two things: an accent outline
    is the card under the pointer, the one a click would take, and the fill is the one
    taken. At most one card is ever filled. Hover is state restyled in the theme pass
    rather than a ``:hover`` rule, as on the app's other cards (``card_frame.py``).

    The icon is extracted art (a tag's medallion) or, for the blank bot, one of the
    app's own glyphs. A card with neither is the same card without the picture: most
    installs have no extracted art.

    With ``radio``, a radio button leads the card and is checked while it is selected,
    for a page whose cards are the answers to one question, as setup's are: a gallery of
    tiles does not say the choice is one of them, and a radio does. It only shows the
    selection; clicks and focus go to the card, as on a card without one.
    """

    chosen = pyqtSignal()
    activated = pyqtSignal()

    def __init__(
        self,
        name: str,
        blurb: str,
        *,
        tooltip: str = "",
        icon: QIcon | None = None,
        glyph: str = "",
        pill: QLabel | None = None,
        detail: str = "",
        radio: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        """``pill`` sits beside the name: BartleBot's templates wear their Source pill
        (BartleBot #149), and the blank bot and setup's answers have none. ``detail`` is a
        last line in small muted text, which BartleBot's Templates dialog uses for a
        template's version and bots (BartleBot #164), and setup for "Recommended"."""
        super().__init__(parent)
        self._selected = False
        self._hovered = False
        self._glyph = glyph
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAccessibleName(name)
        if tooltip:
            self.setToolTip(tooltip)

        row = QHBoxLayout(self)
        row.setContentsMargins(12, 10, 12, 10)
        row.setSpacing(10)
        name_font = THEME.ui_font(THEME.font_body_px + 1, bold=True)
        self._radio: QRadioButton | None = None
        if radio:
            self._radio = QRadioButton()
            self._radio.setAutoExclusive(False)
            self._radio.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            self._radio.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            # As tall as the name's line, so the indicator, drawn centered, sits level with it.
            self._radio.setFixedHeight(QFontMetrics(name_font).height())
            row.addWidget(self._radio, 0, Qt.AlignmentFlag.AlignTop)
        self._icon_view = ArtIconView(SECTION_ICON_PX)
        self._icon_view.set_icon(icon)
        row.addWidget(self._icon_view, 0, Qt.AlignmentFlag.AlignTop)
        self._icon_view.setVisible(icon is not None or bool(glyph))

        # In the row before anything goes into it: a widget added to a layout nothing
        # has installed yet has no parent, and showing one of those opens a window.
        text = QVBoxLayout()
        text.setContentsMargins(0, 0, 0, 0)
        text.setSpacing(2)
        row.addLayout(text, 1)
        self._name = QLabel(name)
        self._name.setFont(name_font)
        self._pill = pill
        if pill is None:
            text.addWidget(self._name)
        else:
            heading = QHBoxLayout()
            heading.setContentsMargins(0, 0, 0, 0)
            heading.setSpacing(6)
            text.addLayout(heading)
            heading.addWidget(self._name, 1)
            heading.addWidget(pill, 0, Qt.AlignmentFlag.AlignVCenter)
        self._blurb = QLabel(blurb)
        self._blurb.setFont(THEME.ui_font(THEME.font_small_px))
        self._blurb.setWordWrap(True)
        text.addWidget(self._blurb)
        self._blurb.setVisible(bool(blurb))
        self._detail = QLabel(detail)
        self._detail.setFont(THEME.ui_font(THEME.font_small_px))
        self._detail.setWordWrap(True)
        text.addWidget(self._detail)
        self._detail.setVisible(bool(detail))
        text.addStretch()
        self.refresh_theme_styles()

    @property
    def name(self) -> str:
        return self._name.text()

    @property
    def detail_text(self) -> str:
        return self._detail.text()

    @property
    def pill_text(self) -> str:
        """What the pill beside the name says; "" on a card with none."""
        return self._pill.text() if self._pill is not None else ""

    @property
    def selected(self) -> bool:
        return self._selected

    @property
    def hovered(self) -> bool:
        return self._hovered

    @property
    def outlined(self) -> bool:
        """Whether the card wears the accent outline: it is selected, or under the pointer."""
        return self._selected or self._hovered

    @property
    def has_icon(self) -> bool:
        return self._icon_view.icon() is not None

    @property
    def radio(self) -> QRadioButton | None:
        """The radio button leading the card, or None on a card built without one."""
        return self._radio

    def set_selected(self, on: bool) -> None:
        if on != self._selected:
            self._selected = on
            if self._radio is not None:
                self._radio.setChecked(on)
            self.refresh_theme_styles()

    def apply_theme_styles(self) -> None:
        border = THEME.accent if self.outlined else THEME.border
        fill = THEME.node_fill_selected if self._selected else THEME.surface
        set_style_sheet(
            self,
            f"StartCard {{ background: {fill}; border: 1px solid {border}; border-radius: 6px; }}"
            f"StartCard QLabel {{ background: transparent; border: none; }}",
        )
        set_style_sheet(self._name, f"color: {THEME.text};")
        set_style_sheet(self._blurb, f"color: {THEME.text_muted};")
        set_style_sheet(self._detail, f"color: {THEME.text_muted};")
        if self._glyph:
            # A glyph is tinted for the theme it was rendered under, unlike art. Rendered
            # well over size, as art is held, so a scaled display draws it down from more
            # pixels than it needs, never up from too few, which smears the edges.
            self._icon_view.set_icon(svg_icon(self._glyph, SECTION_ICON_PX * _GLYPH_OVERSAMPLE))

    def enterEvent(self, event) -> None:
        super().enterEvent(event)
        self._set_hovered(True)

    def leaveEvent(self, event) -> None:
        super().leaveEvent(event)
        self._set_hovered(False)

    def _set_hovered(self, on: bool) -> None:
        if on != self._hovered:
            self._hovered = on
            self.refresh_theme_styles()

    def focusInEvent(self, event) -> None:
        super().focusInEvent(event)
        self.chosen.emit()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.chosen.emit()
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.chosen.emit()
            self.activated.emit()
