"""One icon, drawn at a fixed logical size for whichever screen it is on.

Moved from BartleBot's ``gui/widgets/cards/card_art_tile.py`` in Stage 11 of #167, with
setup's answer cards (:mod:`bartleby.qt.widgets.cards.start_card`), which draw one.
"""

from __future__ import annotations

from PyQt6.QtGui import QIcon, QPainter
from PyQt6.QtWidgets import QWidget


class ArtIconView(QWidget):
    """One extracted icon, drawn at a fixed logical size for whichever screen it is on.

    Stands in for a ``QLabel`` with a pixmap, which would fix the image's resolution
    when it is set rather than when it is painted.
    """

    def __init__(self, size: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._icon: QIcon | None = None
        self.setFixedSize(size, size)

    def icon(self) -> QIcon | None:
        return self._icon

    def set_icon(self, icon: QIcon | None) -> None:
        self._icon = icon
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802  (Qt override)
        if self._icon is None:
            return
        painter = QPainter(self)
        self._icon.paint(painter, self.rect())
        painter.end()
