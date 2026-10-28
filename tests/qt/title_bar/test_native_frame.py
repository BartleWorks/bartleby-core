"""The title bar's frame: which pixels drag, which click, which resize, and what Windows is told.

The rules are ``title_bar_hit``, tested pure. The rest asks a real window the question
Windows asks -- ``WM_NCHITTEST``, sent to its handle -- so it covers the wiring as well: the
window's ``nativeEvent`` answering before Qt's own hit test, which cannot open a menu in the
bar (see the module docstring).
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt

import pytest
from PyQt6.QtGui import QColor

from bartleby.qt.title_bar.native_frame import (
    HTCAPTION,
    HTCLIENT,
    HTTOP,
    HTTOPLEFT,
    HTTOPRIGHT,
    colorref,
    title_bar_hit,
)

_BAR = 35
_WIDTH = 1000
_BAND = 3.2


def _hit(pos, *, maximized=False, over_control=False):
    return title_bar_hit(
        pos, bar_height=_BAR, width=_WIDTH, top_band=_BAND, maximized=maximized, over_control=over_control
    )


# ---------------------------------------------------------------------------
# The rules
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("pos", [(500, _BAR), (500, 400), (-1, 10), (_WIDTH, 10), (500, -1)])
def test_a_point_outside_the_bar_is_left_to_windows(pos):
    assert _hit(pos) is None


def test_the_bar_s_empty_space_drags_the_window():
    assert _hit((500, 20)) == HTCAPTION


def test_a_control_in_the_bar_takes_the_click():
    assert _hit((500, 20), over_control=True) == HTCLIENT


def test_the_top_edge_resizes_even_over_a_control():
    assert _hit((500, 1), over_control=True) == HTTOP


@pytest.mark.parametrize(("pos", "expected"), [((1, 1), HTTOPLEFT), ((_WIDTH - 1, 1), HTTOPRIGHT)])
def test_the_top_corners_resize_diagonally(pos, expected):
    assert _hit(pos) == expected


def test_the_band_ends_where_it_says():
    assert _hit((500, _BAND)) == HTCAPTION


def test_a_maximized_window_has_no_resize_band():
    """The top of the screen is where a pointer flung at the close button lands."""
    assert _hit((_WIDTH - 10, 0), maximized=True, over_control=True) == HTCLIENT
    assert _hit((0, 0), maximized=True) == HTCAPTION


def test_a_colorref_puts_blue_in_the_high_byte():
    assert colorref(QColor(0x44, 0x88, 0xCC)) == 0x00CC8844


# ---------------------------------------------------------------------------
# A real window, asked what Windows asks
# ---------------------------------------------------------------------------

_user32 = ctypes.WinDLL("user32") if hasattr(ctypes, "WinDLL") else None
if _user32 is not None:
    _user32.SendMessageW.restype = ctypes.c_ssize_t
    _user32.SendMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]

_GWL_STYLE = -16
_CAPTION_STYLES = 0x00C00000 | 0x00080000 | 0x00020000 | 0x00010000
