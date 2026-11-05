"""Moved from BartleBuddy's tests/test_layouts.py. Layouts are data the sizing arithmetic reads; Main tiles go to the wizards the user plays."""

import pytest

from bartleby.runtime.game import tile_layouts
from bartleby.runtime.game.game_sizing import aspect_family, rung_tiles
from bartleby.runtime.game.tile_layouts import ALL_EQUAL, ONE_LARGE, Layout, TileSize, arrange, sizes, trade_places

#: The wizard the user plays, and one they don't.
P, F = True, False

#: Two Main tiles: no view uses it, but a layout is data, and the arithmetic takes any count.
TWO_LARGE = Layout("Two large", mains=2)


def test_the_two_views_are_one_large_tile_and_every_tile_equal():
    assert (ONE_LARGE.name, ONE_LARGE.mains, ONE_LARGE.grid) == ("One large", 1, False)
    assert (ALL_EQUAL.name, ALL_EQUAL.grid) == ("All equal", True)


def test_the_presets_and_the_old_words_are_gone():
    for name in ("FOCUS", "PAIR", "GRID", "PRESETS", "layout_for"):
        assert not hasattr(tile_layouts, name)


def test_the_wizards_the_user_plays_get_main_tiles_and_the_rest_side_tiles():
    assert sizes(ONE_LARGE, [F, P, F, F]) == [TileSize.SIDE, TileSize.MAIN, TileSize.SIDE, TileSize.SIDE]
    assert sizes(TWO_LARGE, [P, F, P, F]) == [TileSize.MAIN, TileSize.SIDE, TileSize.MAIN, TileSize.SIDE]
    assert sizes(ALL_EQUAL, [P, F]) == [TileSize.MAIN, TileSize.MAIN]


def test_when_the_user_plays_no_wizard_the_first_client_takes_the_main_tile():
    assert sizes(ONE_LARGE, [F, F]) == [TileSize.MAIN, TileSize.SIDE]


def _overlap(a, b):
    return not (a.x + a.w <= b.x or b.x + b.w <= a.x or a.y + a.h <= b.y or b.y + b.h <= a.y)


@pytest.mark.parametrize("area", [(1920, 1000), (1280, 1000), (2560, 1300)])
def test_one_large_makes_the_main_tile_largest_and_nothing_overlaps(area):
    rects = arrange(ONE_LARGE, [P, F, F, F], area)
    main, *side = rects
    assert all(main.w > s.w * 2 for s in side)
    assert all(r.x >= 0 and r.y >= 0 and r.x + r.w <= area[0] and r.y + r.h <= area[1] for r in rects)
    assert not any(_overlap(a, b) for i, a in enumerate(rects) for b in rects[i + 1 :])


def test_the_main_tile_keeps_the_game_s_aspect():
    main = arrange(ONE_LARGE, [P, F, F, F], (1920, 1000), aspect=16 / 9)[0]
    assert main.w / main.h == pytest.approx(16 / 9, abs=0.01)


def test_the_strip_goes_where_it_leaves_the_larger_main_tile():
    wide = arrange(ONE_LARGE, [P, F, F], (2400, 900))
    tall = arrange(ONE_LARGE, [P, F, F], (1000, 1400))
    # Wide area: the strip stands beside the main tile; tall area: it lies below.
    assert wide[1].x > wide[0].x + wide[0].w - 1
    assert tall[1].y > tall[0].y + tall[0].h - 1


def test_all_equal_tiles_are_equal():
    rects = arrange(ALL_EQUAL, [P, F, F, F], (1920, 1000))
    assert len({(r.w, r.h) for r in rects}) == 1


# -- trading places ----------------------------------------------------------------------------


def test_the_new_main_and_the_old_trade_places_and_the_rest_keep_theirs():
    assert trade_places(["A", "B", "C", "D"], "A", "C") == ["C", "B", "A", "D"]
    assert trade_places(["C", "B", "A", "D"], "C", "B") == ["B", "C", "A", "D"]


@pytest.mark.parametrize(("old", "new"), [("A", "A"), (None, "B"), ("A", None), ("X", "B"), ("A", "X")])
def test_nothing_trades_with_the_same_wizard_or_one_not_placed(old, new):
    assert trade_places(["A", "B", "C"], old, new) == ["A", "B", "C"]


def test_the_strip_s_places_are_handed_out_in_the_placement_order():
    played = [F, F, P, F]
    in_slot_order = arrange(ONE_LARGE, played, (1920, 1000))
    traded = arrange(ONE_LARGE, played, (1920, 1000), order=[2, 1, 0, 3])
    # Slot 2 has the Main tile either way. The strip goes to slots 1, 0 and 3, in that order.
    assert traded[2] == in_slot_order[2]
    assert (traded[1], traded[0], traded[3]) == (in_slot_order[0], in_slot_order[1], in_slot_order[3])


def test_when_the_main_tile_changes_hands_only_those_two_tiles_move():
    area, rungs = (3532, 2051), rung_tiles(aspect_family((1280, 720)))
    before = arrange(ONE_LARGE, [P, F, F, F], area, rung_tiles=rungs, order=[0, 1, 2, 3])
    after = arrange(ONE_LARGE, [F, F, P, F], area, rung_tiles=rungs, order=trade_places([0, 1, 2, 3], 0, 2))

    assert after[2] == before[0]
    assert after[0] == before[2]
    assert after[1] == before[1] and after[3] == before[3]


def test_the_equal_grid_is_in_slot_order_whatever_the_placement():
    assert arrange(ALL_EQUAL, [P, F, F], (1920, 1000), order=[2, 0, 1]) == arrange(ALL_EQUAL, [P, F, F], (1920, 1000))


def test_an_order_that_is_not_one_of_the_tiles_is_refused():
    with pytest.raises(ValueError, match="not an order"):
        arrange(ONE_LARGE, [P, F, F], (1920, 1000), order=[0, 1, 1])
