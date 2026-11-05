"""Tile layouts as data, and the arithmetic that places them.

Moved from BartleBuddy's ``layouts.py`` (Stage 9 of BartleBot #167), which core's
``GameView`` now places its tiles with when a product hands it a layout. A product that
hands none gets BartleBot's equal grid, laid out by ``GameView`` itself.

A client the user plays in needs a window big enough to play; a client the user only
watches does not. So tiles come in two sizes, placed by which wizard the user plays (in
BartleBuddy, the Active wizard):

| tile | holds                                     | size                                              |
|------|-------------------------------------------|---------------------------------------------------|
| Main | the wizard the user plays                 | as large as fits, drawn 1:1, since the user clicks |
| Side | every other wizard                        | small, in a strip beside or below the Main tile   |

Main and Side are this module's words. A player sees the large tile and the small tiles,
and two views (BartleBuddy #14): :data:`ONE_LARGE`, one Main tile and a strip, and
:data:`ALL_EQUAL`, every tile the same size.

A layout is stored as data from the start -- its tiles, their sizes, and that the
wizards the user plays fill its Main tiles -- so the layout editor BartleBuddy's phase 4
brings is an editor of this, and the sizing arithmetic (``game_sizing``) reads the data,
never a layout by name. :func:`arrange` budgets the Main tiles first, then the Side strip,
and puts the strip beside or below, whichever leaves the larger Main tile.

The strip's places are handed out in a placement order the caller keeps, so that a new
wizard taking the Main tile trades places with the one leaving it, and the rest of the
strip stays where the eye left it (:func:`trade_places`).

That is for a client that keeps its launch size, whose game a Side tile cuts. A product
whose clients can be resized hands :func:`arrange` the tile of each rung, and the budget
turns round: every Side tile holds its whole game at the smallest rung, and each Main tile
is exactly the largest rung that fits what is left. Where no such strip fits, or it would
leave a Main tile smaller than the equal grid does, the tiles are the equal grid.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum


class TileSize(StrEnum):
    MAIN = "main"
    SIDE = "side"


@dataclass(frozen=True)
class Layout:
    """A layout: how many Main tiles, filled by the wizards the user plays; every other client goes Side.

    ``grid`` lays every tile out equal instead, BartleBot's layout.
    """

    name: str
    mains: int = 1
    grid: bool = False


#: One large tile for the wizard the user plays, and the rest small.
ONE_LARGE = Layout("One large", mains=1)
#: Every tile the same size.
ALL_EQUAL = Layout("All equal", mains=0, grid=True)


def sizes(layout: Layout, played: Sequence[bool]) -> list[TileSize]:
    """Each client's tile size under *layout*, in slot order, given whether the user plays each."""
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
        # Nobody fills a Main tile (the user plays no wizard): the first client takes it.
        sized[0] = TileSize.MAIN
    return sized


def trade_places[K](order: Sequence[K], old_main: K | None, new_main: K | None) -> list[K]:
    """*order* with the wizard leaving the Main tile put where the one taking it was.

    So when the Main tile changes hands only those two tiles move, and every other Side tile
    keeps its place, as ISBoxer's home regions and xmonad's DwmPromote do it. Nothing changes
    while either is missing from *order*, or when they're the same.
    """
    placed = list(order)
    if old_main == new_main or old_main not in placed or new_main not in placed:
        return placed
    i, j = placed.index(old_main), placed.index(new_main)
    placed[i], placed[j] = placed[j], placed[i]
    return placed


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


def grid_for(client_count: int) -> tuple[int, int]:
    """(rows, cols) for *client_count* tiles: 1x1, 1x2, then square-ish.

    Squarest-that-fits rather than a lookup table, so a fifth client gets a grid rather
    than sharing a cell with the fourth. It agrees with the table it replaced for every
    count that table had an entry for.
    """
    if client_count <= 0:
        return 0, 0
    cols = math.ceil(math.sqrt(client_count))
    rows = math.ceil(client_count / cols)
    return rows, cols


def equal_grid(count: int, w: int, h: int) -> list[Rect]:
    """*count* equal tiles filling w by h, with no gaps: the grid a launch is judged against."""
    rows, cols = grid_for(count)
    if not rows:
        return []
    tile_w, tile_h = w // cols, h // rows
    return [Rect((i % cols) * tile_w, (i // cols) * tile_h, tile_w, tile_h) for i in range(count)]


def _largest_tile(w: float, h: float, rung_tiles: Sequence[tuple[int, int]]) -> tuple[int, int] | None:
    """The largest of *rung_tiles*, smallest first, inside w by h, or None if none is."""
    return next((tile for tile in reversed(rung_tiles) if tile[0] <= w and tile[1] <= h), None)


def _whole_strip(
    mains: int, sides: int, w: int, h: int, rung_tiles: Sequence[tuple[int, int]]
) -> tuple[list[Rect], list[Rect]] | None:
    """The Main and Side tiles of a strip that holds its games whole, or None if there is none.

    The strip reserves the smallest rung's tile for each Side client, beside the Main tiles
    or below them, whichever leaves the larger Main tile. On a tie it's whichever gives the
    larger Side tiles, since the Main tile is the same either way, and beside if those tie
    too. Each Main tile is exactly its rung's tile, and the Side tiles share everything else.

    There is none if the reservation does not fit, if a Main tile comes out at the smallest
    rung, which leaves a Side tile no rung below it, or if it comes out smaller than the
    equal grid would make it. That last one is what a launch's fit relies on: it judges the
    equal grid, so a launch it allows always fits a Main tile.
    """
    side_w, side_h = rung_tiles[0]
    rank = {tile: i for i, tile in enumerate(rung_tiles)}
    strips = []
    if sides * side_h + GAP * (sides - 1) <= h:
        main = _largest_tile((w - side_w - GAP * mains) / mains, h, rung_tiles)
        if main is not None:
            strips.append(_strip_beside(main, mains, sides, w, h))
    if sides * side_w + GAP * (sides - 1) <= w:
        main = _largest_tile((w - GAP * (mains - 1)) / mains, h - side_h - GAP, rung_tiles)
        if main is not None:
            strips.append(_strip_below(main, mains, sides, w, h))
    if not strips:
        return None

    def ranked(strip: tuple[list[Rect], list[Rect]]) -> tuple[int, int]:
        main_rect, side_rect = strip[0][0], strip[1][0]
        side = _largest_tile(side_rect.w, side_rect.h, rung_tiles)
        return rank[(main_rect.w, main_rect.h)], -1 if side is None else rank[side]

    # max keeps the first of equals, and beside is first.
    best = max(strips, key=ranked)
    main_rank = ranked(best)[0]
    if main_rank == 0:
        return None
    rows, cols = grid_for(mains + sides)
    in_a_grid = _largest_tile(w // cols, h // rows, rung_tiles)
    if in_a_grid is not None and main_rank < rank[in_a_grid]:
        return None
    return best


def _strip_beside(main: tuple[int, int], mains: int, sides: int, w: int, h: int) -> tuple[list[Rect], list[Rect]]:
    main_w, main_h = main
    y = (h - main_h) // 2
    main_rects = [Rect(i * (main_w + GAP), y, main_w, main_h) for i in range(mains)]
    strip_x = mains * (main_w + GAP)
    share = (h - GAP * (sides - 1)) // sides
    return main_rects, [Rect(strip_x, i * (share + GAP), w - strip_x, share) for i in range(sides)]


def _strip_below(main: tuple[int, int], mains: int, sides: int, w: int, h: int) -> tuple[list[Rect], list[Rect]]:
    main_w, main_h = main
    x = (w - (mains * main_w + GAP * (mains - 1))) // 2
    main_rects = [Rect(x + i * (main_w + GAP), 0, main_w, main_h) for i in range(mains)]
    strip_y = main_h + GAP
    share = (w - GAP * (sides - 1)) // sides
    return main_rects, [Rect(i * (share + GAP), strip_y, share, h - strip_y) for i in range(sides)]


def strip_fits(
    layout: Layout, played: Sequence[bool], area: tuple[int, int], rung_tiles: Sequence[tuple[int, int]]
) -> bool:
    """Whether *layout* has Side tiles here, by rung, rather than falling back to the equal grid."""
    tile_sizes = sizes(layout, played)
    if layout.grid or TileSize.SIDE not in tile_sizes:
        return False
    mains, sides = tile_sizes.count(TileSize.MAIN), tile_sizes.count(TileSize.SIDE)
    return _whole_strip(mains, sides, *area, rung_tiles) is not None


def arrange(
    layout: Layout,
    played: Sequence[bool],
    area: tuple[int, int],
    aspect: float = 4 / 3,
    rung_tiles: Sequence[tuple[int, int]] = (),
    order: Sequence[int] | None = None,
) -> list[Rect]:
    """Each client's tile, in slot order, inside an *area* of (width, height) pixels.

    Main tiles share what the strip leaves; the strip goes beside or below the Main tiles,
    whichever leaves the larger Main tile.

    *rung_tiles* is what a product whose clients can be resized hands in: the tile of each
    rung of the clients' family, smallest first (``game_sizing.rung_tiles``), which is
    what lets this module stay below ``game_sizing``. Then the module docstring's other
    budget applies, and *aspect* is not used.

    *order* is the placement order: each client's index in slot order, in the order the
    strip's places are handed out, which :func:`trade_places` keeps. None is slot order. The
    equal grid is always in slot order.
    """
    w, h = area
    tile_sizes = sizes(layout, played)
    if not tile_sizes:
        return []
    mains = tile_sizes.count(TileSize.MAIN)
    sides = tile_sizes.count(TileSize.SIDE)
    if rung_tiles:
        strip = None if layout.grid or not sides else _whole_strip(mains, sides, w, h, rung_tiles)
        if strip is None:
            return equal_grid(len(tile_sizes), w, h)
        return _hand_out(tile_sizes, strip[0], strip[1], order)
    if layout.grid or TileSize.SIDE not in tile_sizes:
        return _grid(len(tile_sizes), w, h, aspect)
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
    return _hand_out(tile_sizes, main_rects, side_rects, order)


def _hand_out(
    tile_sizes: Sequence[TileSize], main_rects: list[Rect], side_rects: list[Rect], order: Sequence[int] | None
) -> list[Rect]:
    """Each client's rect in slot order, the Main and Side places handed out in *order*."""
    placing = range(len(tile_sizes)) if order is None else order
    if sorted(placing) != list(range(len(tile_sizes))):
        raise ValueError(f"{list(placing)} is not an order of {len(tile_sizes)} tiles")
    mains_iter, sides_iter = iter(main_rects), iter(side_rects)
    rects: list[Rect | None] = [None] * len(tile_sizes)
    for index in placing:
        rects[index] = next(mains_iter) if tile_sizes[index] is TileSize.MAIN else next(sides_iter)
    return rects
