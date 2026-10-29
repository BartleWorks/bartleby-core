"""Moved from BartleBuddy's tests/test_layouts.py. Layouts are data the sizing arithmetic reads; Main tiles go to the wizards the user moves."""

import pytest

from bartleby.runtime.game.tile_layouts import FOCUS, GRID, PAIR, TileSize, arrange, layout_for, sizes

#: A wizard the user moves, and one BartleBuddy moves.
P, F = True, False


def test_the_layout_follows_who_the_user_moves():
    assert layout_for([P, F, F, F]) is FOCUS
    assert layout_for([P, P, F, F]) is PAIR
    assert layout_for([P, P, P, P]) is GRID


def test_the_wizards_the_user_moves_get_main_tiles_and_the_rest_side_tiles():
    assert sizes(FOCUS, [F, P, F, F]) == [TileSize.SIDE, TileSize.MAIN, TileSize.SIDE, TileSize.SIDE]
    assert sizes(PAIR, [P, F, P, F]) == [TileSize.MAIN, TileSize.SIDE, TileSize.MAIN, TileSize.SIDE]


def test_when_the_user_moves_no_wizard_the_first_client_takes_the_main_tile():
    assert sizes(FOCUS, [F, F]) == [TileSize.MAIN, TileSize.SIDE]


def _overlap(a, b):
    return not (a.x + a.w <= b.x or b.x + b.w <= a.x or a.y + a.h <= b.y or b.y + b.h <= a.y)


@pytest.mark.parametrize("area", [(1920, 1000), (1280, 1000), (2560, 1300)])
def test_focus_makes_the_main_tile_largest_and_nothing_overlaps(area):
    rects = arrange(FOCUS, [P, F, F, F], area)
    main, *side = rects
    assert all(main.w > s.w * 2 for s in side)
    assert all(r.x >= 0 and r.y >= 0 and r.x + r.w <= area[0] and r.y + r.h <= area[1] for r in rects)
    assert not any(_overlap(a, b) for i, a in enumerate(rects) for b in rects[i + 1 :])


def test_the_main_tile_keeps_the_game_s_aspect():
    main = arrange(FOCUS, [P, F, F, F], (1920, 1000), aspect=16 / 9)[0]
    assert main.w / main.h == pytest.approx(16 / 9, abs=0.01)


def test_the_strip_goes_where_it_leaves_the_larger_main_tile():
    wide = arrange(FOCUS, [P, F, F], (2400, 900))
    tall = arrange(FOCUS, [P, F, F], (1000, 1400))
    # Wide area: the strip stands beside the main tile; tall area: it lies below.
    assert wide[1].x > wide[0].x + wide[0].w - 1
    assert tall[1].y > tall[0].y + tall[0].h - 1


def test_grid_tiles_are_equal():
    rects = arrange(GRID, [P, P, P, P], (1920, 1000))
    assert len({(r.w, r.h) for r in rects}) == 1
