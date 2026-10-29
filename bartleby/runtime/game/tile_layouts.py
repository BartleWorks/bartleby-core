"""Tile layouts as data, and the arithmetic that places them.

Moved from BartleBuddy's ``layouts.py`` (Stage 9 of BartleBot #167), which core's
``GameView`` now places its tiles with when a product hands it a layout. A product that
hands none gets BartleBot's equal grid, laid out by ``GameView`` itself.

A client the user plays in needs a window big enough to play; a client the user only
watches does not. So tiles come in two sizes, placed by who moves each wizard (in
BartleBuddy, its ``control``):

| tile | holds                                     | size                                              |
|------|-------------------------------------------|---------------------------------------------------|
| Main | a wizard the user moves                   | as large as fits, drawn 1:1, since the user clicks |
| Side | a wizard BartleBuddy moves                | small, in a strip beside or below the Main tiles  |

A layout is stored as data from the start -- its tiles, their sizes, and that the
wizards the user moves fill its Main tiles -- so the layout editor BartleBuddy's phase 4
brings is an editor of this, and the sizing arithmetic (``game_sizing``) reads the data,
never a layout by name. Focus, Pair and Grid are its first
presets. :func:`arrange` budgets the Main tiles first, then the Side strip, and puts the
strip beside or below, whichever leaves the larger Main tile.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum


class TileSize(StrEnum):
    MAIN = "main"
    SIDE = "side"


@dataclass(frozen=True)
class Layout:
    """A layout: how many Main tiles, filled by the wizards the user moves; every other client goes Side.

    ``grid`` lays every tile out equal instead, BartleBot's layout, for Manual on every
    client and for Autopilot.
    """

    name: str
    mains: int = 1
    grid: bool = False


FOCUS = Layout("Focus", mains=1)
PAIR = Layout("Pair", mains=2)
GRID = Layout("Grid", mains=0, grid=True)

PRESETS: tuple[Layout, ...] = (FOCUS, PAIR, GRID)


def layout_for(played: Sequence[bool]) -> Layout:
    """The preset for *played*, each wizard's "the user moves it": one is Focus, two Pair, otherwise Grid."""
    players = sum(1 for p in played if p)
    if players == len(played) or players == 0:
        return GRID
    return FOCUS if players == 1 else PAIR


def sizes(layout: Layout, played: Sequence[bool]) -> list[TileSize]:
    """Each client's tile size under *layout*, in slot order, given whether the user moves each."""
    if layout.grid:
        return [TileSize.MAIN] * len(played)
    sized, mains = [], 0
    for user_moves in played:
        if user_moves and mains < layout.mains:
            sized.append(TileSize.MAIN)
            mains += 1
        else:
            sized.append(TileSize.SIDE)
    if mains == 0 and played:
        # Nobody fills a Main tile (the user moves no wizard): the first client takes it.
        sized[0] = TileSize.MAIN
    return sized


@dataclass(frozen=True)
class Rect:
    x: int
    y: int
    w: int
    h: int


#: The smallest Side tile, as a share of the area's shorter side, before the strip gives up room.
SIDE_SHARE = 0.25
#: Pixels between tiles.
GAP = 6


def _fit(w: float, h: float, aspect: float) -> tuple[int, int]:
    """The largest *aspect* (width over height) rectangle inside w by h."""
    if w <= 0 or h <= 0:
        return 0, 0
    if w / h > aspect:
        return int(h * aspect), int(h)
    return int(w), int(w / aspect)


def _row(count: int, x: int, y: int, w: int, h: int, aspect: float, *, vertical: bool) -> list[Rect]:
    """*count* equal tiles of *aspect* in a row (or a column), each as large as fits."""
    if count == 0:
        return []
    gaps = GAP * (count - 1)
    tw, th = _fit((w - gaps) / count, h, aspect) if not vertical else _fit(w, (h - gaps) / count, aspect)
    rects = []
    for i in range(count):
        if vertical:
            rects.append(Rect(x + (w - tw) // 2, y + i * (th + GAP), tw, th))
        else:
            rects.append(Rect(x + i * (tw + GAP), y + (h - th) // 2, tw, th))
    return rects


def _grid(count: int, w: int, h: int, aspect: float) -> list[Rect]:
    best: list[Rect] = []
    for cols in range(1, count + 1):
        rows = -(-count // cols)
        tw, th = _fit((w - GAP * (cols - 1)) / cols, (h - GAP * (rows - 1)) / rows, aspect)
        if best and tw <= best[0].w:
            continue
        best = [Rect((i % cols) * (tw + GAP), (i // cols) * (th + GAP), tw, th) for i in range(count)]
    return best


def arrange(layout: Layout, played: Sequence[bool], area: tuple[int, int], aspect: float = 4 / 3) -> list[Rect]:
    """Each client's tile, in slot order, inside an *area* of (width, height) pixels.

    Main tiles share what the strip leaves; the strip goes beside or below the Main tiles,
    whichever leaves the larger Main tile.
    """
    w, h = area
    tile_sizes = sizes(layout, played)
    if not tile_sizes:
        return []
    if layout.grid or TileSize.SIDE not in tile_sizes:
        return _grid(len(tile_sizes), w, h, aspect)
    mains = tile_sizes.count(TileSize.MAIN)
    sides = tile_sizes.count(TileSize.SIDE)
    options = []
    for strip in ("beside", "below"):
        if strip == "beside":
            strip_w = int(min(w, h) * SIDE_SHARE * aspect)
            main_rects = _row(mains, 0, 0, w - strip_w - GAP, h, aspect, vertical=False)
            side_rects = _row(sides, w - strip_w, 0, strip_w, h, aspect, vertical=True)
        else:
            strip_h = int(min(w, h) * SIDE_SHARE)
            main_rects = _row(mains, 0, 0, w, h - strip_h - GAP, aspect, vertical=False)
            side_rects = _row(sides, 0, h - strip_h, w, strip_h, aspect, vertical=False)
        options.append((main_rects[0].w if main_rects else 0, main_rects, side_rects))
    _, main_rects, side_rects = max(options, key=lambda o: o[0])
    mains_iter, sides_iter = iter(main_rects), iter(side_rects)
    return [next(mains_iter) if s is TileSize.MAIN else next(sides_iter) for s in tile_sizes]
