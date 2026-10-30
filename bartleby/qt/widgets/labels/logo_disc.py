"""The logo on its disc, ringed in the accent, as the launch screen shows it (BartleBot #26).

Moved from BartleBot's ``gui/widgets/labels/logo_disc.py``. The disc is handed in as a
function that draws it *n* pixels across, which on the launch screen is
:func:`bartleby.qt.logo.logo_disc_image`. The ring and its sizing are here.
"""

from __future__ import annotations

import math
from collections.abc import Callable

from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QColor, QImage, QPainter, QPen
from PyQt6.QtWidgets import QWidget

from bartleby.qt.theming.theme import THEME

#: The accent ring around the disc, and the gap between the two, in logical pixels.
RING_WIDTH = 2
RING_GAP = 4


def ringed_disc_image(side: int, disc: QImage, ring: int, color: QColor) -> QImage:
    """A *side* square: the image *disc* in its middle, ringed by *ring* pixels of *color*.

    The disc goes in at a whole pixel offset, so its cells stay sharp; only the ring is
    antialiased. The ring runs along the square's edge, and whatever rounding the
    physical sizes bring lands in the gap.
    """
    margin = (side - disc.width()) // 2
    image = QImage(side, side, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.drawImage(margin, margin, disc)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QPen(color, ring))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    center = margin + disc.width() / 2
    radius = disc.width() / 2 + (margin - ring) + ring / 2
    painter.drawEllipse(QRectF(center - radius, center - radius, 2 * radius, 2 * radius))
    painter.end()
    return image


class LogoDisc(QWidget):
    """The logo on a disc *diameter* logical pixels across, ringed in ``THEME.accent``.

    *disc_image* draws the disc at a size in physical pixels.

    The widget is the ring's width and gap larger than the disc on every side.

    Drawn at the screen's physical pixel size rather than scaled from a logical
    one, because the cells only stay sharp when each is a whole number of the
    pixels actually on screen; at 125% a 160 px disc is 200 of them.

    The accent is read at paint time, so a theme switch needs nothing of its own.
    """

    def __init__(self, diameter: int, disc_image: Callable[[int], QImage], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._diameter = diameter
        self._disc_image = disc_image
        side = diameter + 2 * (RING_GAP + RING_WIDTH)
        self.setFixedSize(side, side)
        self._image: QImage | None = None
        self._key: tuple[float, str] | None = None

    def paintEvent(self, event) -> None:
        dpr = self.devicePixelRatioF()
        key = (dpr, THEME.accent)
        if self._image is None or self._key != key:
            self._image = ringed_disc_image(
                math.floor(self.width() * dpr),
                self._disc_image(math.floor(self._diameter * dpr)),
                max(1, round(RING_WIDTH * dpr)),
                QColor(THEME.accent),
            )
            self._image.setDevicePixelRatio(dpr)
            self._key = key
        painter = QPainter(self)
        painter.drawImage(0, 0, self._image)
        painter.end()
