"""The two shapes a tag takes on screen, and the one palette behind both.

A tag is colored by its group everywhere it appears — the dot in the picker, the
pill on a library card, the chip in a filter bar, the chip beside an editor. Someone who
filters by one group and scans the results should be matching a color rather than
re-reading every pill, and that only holds if one vocabulary owns the mapping.

Two shapes, because they do different jobs:

* :class:`TagPill` — descriptive. Small, inert, several per row. It says what a thing
  is; there is nothing to click.
* :class:`TagChip` — interactive. Larger, with a ✕ and a real hit target, because
  removing the filter or the tag it stands for is the whole point of it being there.

Both take their color from the same call, so the sizes differ and the hues never do.

Both are also led by the tag's game medallion where the user has extracted one (#29),
at ``TAG_ICON_PX``: the pill is exactly as tall as the medallion, so the ring forms its
rounded left end and the colored body runs on from it, name included. Without extracted
art, which is most installs, both are exactly the text forms above.

The pills that claim no tag color, or another palette's (``NeutralPill``, ``ActionPill``,
``NeutralChip``, ``AccentPill``, ``AccountPill``, ``WarningPill``), and the metrics every
pill shares, are ``pill.py``'s.

Moved from BartleBot's ``gui/widgets/pills/tag_pill.py``, whose pills about a bot and a keep
list stayed.
"""

from __future__ import annotations

from PyQt6.QtCore import QEvent, QRect, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QIcon, QPainter, QPainterPath
from PyQt6.QtWidgets import QLabel, QPushButton, QSizePolicy, QWidget

from bartleby.qt.theming.theme import THEME
from bartleby.qt.theming.themed import set_style_sheet
from bartleby.qt.widgets.pickers.tag_picker import TAG_ICON_PX, TagVocabulary, tag_icon
from bartleby.qt.widgets.pills.pill import pill_height, pill_style

#: The space either side of the medallion pill's text. The pill is as tall as the
#: medallion, so its text is a size up from the plain pill's -- see ``medallion_font``.
_MEDALLION_GAP = 4  # medallion to text
_MEDALLION_PAD_R = 8  # text to the round right end


def medallion_font(base: QFont) -> QFont:
    """*base* at the medallion pill's size, bold."""
    font = QFont(base)
    font.setPixelSize(THEME.font_subtext_px)
    font.setBold(True)
    return font


def medallion_pill_width(font: QFont, text: str) -> int:
    """How wide a medallion pill carrying *text* in *font* is."""
    return TAG_ICON_PX + _MEDALLION_GAP + QFontMetrics(font).horizontalAdvance(text) + _MEDALLION_PAD_R


def paint_medallion_pill(painter: QPainter, width: int, icon: QIcon, text: str, fill: str, fg: str) -> None:
    """A pill whose left end *is* the medallion.

    The pill is exactly as tall as the medallion and every medallion fills its square, so
    the ring and the pill's round end are the same circle. The fill starts at the
    medallion's center rather than at its edge: a rounded rectangle starting at the edge
    would curve away under the ring and leave a notch where the top and bottom edges meet
    it. The right end is a half circle of the same radius.
    """
    h = TAG_ICON_PX
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    body = QPainterPath()
    body.setFillRule(Qt.FillRule.WindingFill)
    body.addRect(QRectF(h / 2, 0, max(width - h, 0), h))
    body.addEllipse(QRectF(width - h, 0, h, h))
    painter.fillPath(body, QColor(fill))
    icon.paint(painter, QRect(0, 0, h, h))
    painter.setPen(QColor(fg))
    text_x = h + _MEDALLION_GAP
    painter.drawText(
        QRect(text_x, 0, max(width - text_x, 0), h),
        int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
        text,
    )


class TagPill(QLabel):
    """A small, inert pill naming one tag, led by its medallion where one was extracted.

    The medallion form is drawn rather than styled (see :func:`paint_medallion_pill`): a
    stylesheet cannot put an icon flush with a rounded end. The text form, group-colored,
    is every other case: an install with no extracted art.

    The name stays beside the medallion. The art alone was tried and was not enough to
    read a card by.
    """

    def __init__(self, tag: str, parent: QWidget | None = None, *, vocabulary: TagVocabulary) -> None:
        super().__init__(tag, parent)
        self._tag = tag
        self._vocabulary = vocabulary
        self._applying_style = False
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._icon = tag_icon(tag)
        if self._icon is None:
            self.setFixedHeight(pill_height())
        else:
            self.setFont(medallion_font(self.font()))
            self.setFixedSize(medallion_pill_width(self.font(), tag), TAG_ICON_PX)
        self._restyle()

    @property
    def tag(self) -> str:
        return self._tag

    @property
    def has_icon(self) -> bool:
        """Whether this pill is led by the tag's medallion."""
        return self._icon is not None

    def paintEvent(self, event) -> None:
        if self._icon is None:
            super().paintEvent(event)
            return
        painter = QPainter(self)
        painter.setFont(self.font())
        paint_medallion_pill(
            painter, self.width(), self._icon, self._tag, self._vocabulary.accent(self._tag), THEME.pill_text
        )
        painter.end()

    def _restyle(self) -> None:
        # Guard is load-bearing: setStyleSheet delivers StyleChange straight back to
        # changeEvent, and the app dies in window construction with no traceback.
        # Same shape as PickerButton._apply_style.
        if self._applying_style:
            return
        self._applying_style = True
        try:
            if self._icon is not None:
                # paintEvent draws it all, reading THEME at paint time; a stylesheet font
                # would override the one the width was measured with.
                set_style_sheet(self, "background: transparent;")
            else:
                set_style_sheet(self, pill_style(self._vocabulary.accent(self._tag), THEME.pill_text))
        finally:
            self._applying_style = False

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.StyleChange:
            self._restyle()


class TagChip(QPushButton):
    """A group-colored chip that removes its tag when clicked.

    Led by the tag's medallion where one was extracted, in the same shape as
    :class:`TagPill`'s, so a tag reads the same in the filter bar as on the cards below it.
    """

    removed = pyqtSignal(str)

    def __init__(
        self,
        tag: str,
        *,
        vocabulary: TagVocabulary,
        tooltip: str | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(f"{tag}  ✕", parent)
        self._tag = tag
        self._vocabulary = vocabulary
        self._applying_style = False
        self._icon = tag_icon(tag)
        if self._icon is not None:
            # Hover repaints need WA_Hover once the stylesheet no longer draws the chip.
            self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
            self.setFont(medallion_font(self.font()))
            self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
            self.setFixedSize(medallion_pill_width(self.font(), self.text()), TAG_ICON_PX)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(tooltip or f"Remove the {tag} tag")
        self._restyle()
        self.clicked.connect(lambda: self.removed.emit(self._tag))

    @property
    def tag(self) -> str:
        return self._tag

    @property
    def has_icon(self) -> bool:
        """Whether this chip is led by the tag's medallion."""
        return self._icon is not None

    def paintEvent(self, event) -> None:
        if self._icon is None:
            super().paintEvent(event)
            return
        painter = QPainter(self)
        painter.setFont(self.font())
        fill = THEME.edge_selected if self.underMouse() else self._vocabulary.accent(self._tag)
        paint_medallion_pill(painter, self.width(), self._icon, self.text(), fill, THEME.pill_text)
        painter.end()

    def _restyle(self) -> None:
        if self._applying_style:  # see TagPill._restyle
            return
        self._applying_style = True
        try:
            if self._icon is not None:
                set_style_sheet(self, "QPushButton { border: none; background: transparent; }")
                return
            base = pill_style(
                self._vocabulary.accent(self._tag),
                THEME.pill_text,
                font_size=THEME.font_subtext_px,
                radius=9,
                pad="2px 9px",
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
