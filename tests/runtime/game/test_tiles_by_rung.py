"""Tiles by rung: whole Side tiles, Main in what is left, and the size each client should run at.

The tables are the Live Client Resize plan's (BartleBuddy #3), at 100% display scale with a
40-pixel taskbar and BartleBuddy's 300-pixel sidebar shown or hidden. Nothing here reads a
pixel of a real window: ``tests/qt/game_embed/test_game_view_layout.py`` holds the game view
to this arithmetic.
"""

from __future__ import annotations

import functools

import pytest

from bartleby.runtime.game import game_sizing as gs
from bartleby.runtime.game.game_sizing import VALID_RESOLUTIONS, aspect_family, fitting_rung, tile_size, tile_targets
from bartleby.runtime.game.tile_layouts import (
    FOCUS,
    GRID,
    PAIR,
    TileSize,
    arrange,
    equal_grid,
    sizes,
    strip_fits,
)

WIDE, NARROW = (1280, 720), (1024, 768)

HIDDEN_1080, SHOWN_1080 = (1920, 971), (1612, 971)
SHOWN_1440, SHOWN_4K = (2252, 1331), (3532, 2051)

DISPLAY_SCALES = (1.0, 1.25, 1.5, 2.0)

#: Every shape of session up to four clients: a layout and who the user moves.
SESSIONS = [
    (FOCUS, [True]),
    (GRID, [True]),
    (FOCUS, [True, False]),
    (GRID, [True, True]),
    (FOCUS, [True, False, False]),
    (PAIR, [True, True, False]),
    (FOCUS, [True, False, False, False]),
    (PAIR, [True, True, False, False]),
    (GRID, [True, True, True, True]),
]

AREAS = [(w, h) for w in range(800, 4000, 160) for h in range(500, 2300, 120)]


def _placed(layout, played, area, launch, dpr=1.0):
    """What a game view does with a session: its tiles' rects, and each client's target."""
    return _placed_once(layout, tuple(played), area, launch, dpr)


@functools.cache
def _placed_once(layout, played, area, launch, dpr):
    family = aspect_family(launch)
    tiles = gs.rung_tiles(family, dpr)
    rects = arrange(layout, played, area, rung_tiles=tiles)
    kinds = sizes(layout, played) if strip_fits(layout, played, area, tiles) else len(played)
    targets = tile_targets([launch] * len(played), [(r.w, r.h) for r in rects], kinds, dpr)
    return rects, kinds, targets


def _main_and_side(layout, played, area, launch):
    _, kinds, targets = _placed(layout, played, area, launch)
    assert kinds != len(played), "no strip fit"
    main = {t for t, k in zip(targets, kinds) if k is TileSize.MAIN}
    side = {t for t, k in zip(targets, kinds) if k is TileSize.SIDE}
    assert len(main) == len(side) == 1
    return main.pop(), side.pop()


# -- the plan's tables, row by row ------------------------------------------------------------


@pytest.mark.parametrize(
    ("launch", "area", "main", "side"),
    [
        (WIDE, HIDDEN_1080, (1280, 720), (534, 300)),
        (WIDE, SHOWN_1080, (960, 540), (534, 300)),
        (NARROW, HIDDEN_1080, (1024, 768), (800, 600)),
        (NARROW, SHOWN_1080, (1024, 768), (480, 360)),
    ],
)
def test_two_accounts_on_1080p_one_following(launch, area, main, side):
    assert _main_and_side(FOCUS, [True, False], area, launch) == (main, side)


@pytest.mark.parametrize(
    ("area", "followers", "main", "side"),
    [
        (HIDDEN_1080, 1, (1280, 720), (534, 300)),
        (HIDDEN_1080, 2, (1280, 720), (534, 300)),
        (HIDDEN_1080, 3, (960, 540), (534, 300)),
        (SHOWN_1080, 1, (960, 540), (534, 300)),
        (SHOWN_1080, 2, (960, 540), (534, 300)),
        (SHOWN_1440, 1, (1600, 900), (534, 300)),
        (SHOWN_1440, 2, (1600, 900), (534, 300)),
        (SHOWN_1440, 3, (1600, 900), (534, 300)),
        (SHOWN_4K, 1, (2560, 1440), (854, 480)),
        (SHOWN_4K, 2, (2560, 1440), (854, 480)),
        (SHOWN_4K, 3, (2560, 1440), (854, 480)),
    ],
)
def test_one_main_on_other_screens_launched_at_16_9(area, followers, main, side):
    assert _main_and_side(FOCUS, [True] + [False] * followers, area, WIDE) == (main, side)


def test_three_followers_on_1080p_with_the_sidebar_hidden_lie_in_a_row_below():
    rects, _, _ = _placed(FOCUS, [True, False, False, False], HIDDEN_1080, WIDE)
    main, *side = rects
    assert all(s.y >= main.y + main.h for s in side)


def test_three_followers_on_1080p_with_the_sidebar_shown_are_a_grid_of_four_at_746x420():
    """The launch fit's own largest, for four clients in this area, and each target is the launch."""
    area, launch = SHOWN_1080, gs.largest_fitting(list(aspect_family(WIDE)), 4, SHOWN_1080)
    assert launch == (746, 420)

    rects, kinds, targets = _placed(FOCUS, [True, False, False, False], area, launch)

    assert kinds == 4
    assert targets == [launch] * 4
    assert [(r.x, r.y, r.w, r.h) for r in rects] == [(r.x, r.y, r.w, r.h) for r in equal_grid(4, *area)]


def test_two_main_tiles_on_1080p_with_the_sidebar_hidden():
    assert _main_and_side(PAIR, [True, True, False], HIDDEN_1080, WIDE) == ((854, 480), (640, 360))


# -- the families ------------------------------------------------------------------------------


def test_each_family_is_its_aspect_s_offered_sizes_smallest_first():
    assert aspect_family((1280, 720)) == (
        (534, 300),
        (640, 360),
        (746, 420),
        (854, 480),
        (960, 540),
        (1280, 720),
        (1366, 768),
        (1600, 900),
        (1920, 1080),
        (2560, 1440),
        (3840, 2160),
    )
    assert aspect_family((800, 600)) == (
        (480, 360),
        (560, 420),
        (640, 480),
        (720, 540),
        (800, 600),
        (1024, 768),
        (1600, 1200),
        (2048, 1536),
    )


@pytest.mark.parametrize("resolution", [(1280, 1024), (1680, 1050), (1920, 1200), (1000, 1000)])
def test_any_other_aspect_has_no_ladder(resolution):
    assert aspect_family(resolution) is None


def test_every_offered_size_of_16_9_or_4_3_is_in_its_own_family():
    for resolution in VALID_RESOLUTIONS:
        family = aspect_family(resolution)
        assert family is None or resolution in family


def test_a_family_is_found_for_a_size_the_app_does_not_offer():
    """A client started outside the app at a size only the game's own dropdown has."""
    assert aspect_family((1760, 990)) == aspect_family(WIDE)


def test_the_fitting_rung_is_the_largest_whose_whole_tile_fits():
    family = aspect_family(WIDE)
    tile_w, tile_h = tile_size((960, 540))

    assert fitting_rung((tile_w, tile_h), family) == (960, 540)
    assert fitting_rung((tile_w - 1, tile_h), family) == (854, 480)
    assert fitting_rung((tile_w, tile_h - 1), family) == (854, 480)


def test_the_fitting_rung_with_nothing_fitting_is_the_family_s_smallest():
    assert fitting_rung((10, 10), aspect_family(WIDE)) == (534, 300)


def test_the_fitting_rung_measures_at_the_display_s_scale():
    """1280x720 physical is 640x360 logical at 200%, plus the tile's chrome in logical pixels."""
    assert fitting_rung(tile_size((1280, 720), 2.0), aspect_family(WIDE), 2.0) == (1280, 720)


# -- what holds everywhere ---------------------------------------------------------------------


@functools.cache
def _every_placement():
    """Every session in every area at every scale, at each launch the fit allows there."""
    placements = []
    for dpr in DISPLAY_SCALES:
        for area in AREAS:
            for family_of in (WIDE, NARROW):
                family = aspect_family(family_of)
                for layout, played in SESSIONS:
                    allowed = [r for r in family if gs.fits(r, len(played), area, dpr)]
                    # The smallest and the largest bound what the rungs between can do.
                    for launch in {allowed[0], allowed[-1]} if allowed else ():
                        placements.append((dpr, area, family, layout, played, launch))
    return placements


def test_every_target_is_an_offered_size_of_the_client_s_aspect_and_its_tile_holds_it_whole():
    checked = 0
    for dpr, area, family, layout, played, launch in _every_placement():
        rects, _, targets = _placed(layout, played, area, launch, dpr)
        for rect, target in zip(rects, targets):
            assert target in VALID_RESOLUTIONS
            assert target in family
            need_w, need_h = tile_size(target, dpr)
            assert need_w <= rect.w and need_h <= rect.h, (area, dpr, played, launch, target, rect)
            checked += 1
    assert checked > 10_000


def test_every_tile_stays_inside_the_area_and_none_overlaps():
    for dpr, area, _, layout, played, launch in _every_placement():
        rects, _, _ = _placed(layout, played, area, launch, dpr)
        for i, a in enumerate(rects):
            assert a.x >= 0 and a.y >= 0 and a.x + a.w <= area[0] and a.y + a.h <= area[1]
            for b in rects[i + 1 :]:
                assert a.x + a.w <= b.x or b.x + b.w <= a.x or a.y + a.h <= b.y or b.y + b.h <= a.y


def test_a_side_target_is_always_a_rung_below_main_s():
    seen = 0
    for dpr, area, family, layout, played, launch in _every_placement():
        _, kinds, targets = _placed(layout, played, area, launch, dpr)
        if kinds == len(played):
            continue
        mains = {t for t, k in zip(targets, kinds) if k is TileSize.MAIN}
        assert len(mains) == 1
        main = family.index(mains.pop())
        assert all(family.index(t) < main for t, k in zip(targets, kinds) if k is TileSize.SIDE)
        seen += 1
    assert seen > 1_000


def test_each_main_tile_is_exactly_the_tile_of_its_rung():
    for dpr, area, _, layout, played, launch in _every_placement():
        rects, kinds, targets = _placed(layout, played, area, launch, dpr)
        if kinds == len(played):
            continue
        for rect, kind, target in zip(rects, kinds, targets):
            if kind is TileSize.MAIN:
                assert (rect.w, rect.h) == tile_size(target, dpr)


def test_a_launch_the_fit_allows_fits_the_main_tile_whichever_way_the_strip_lies():
    shapes = set()
    for dpr, area, _, layout, played, launch in _every_placement():
        rects, kinds, _ = _placed(layout, played, area, launch, dpr)
        need_w, need_h = tile_size(launch, dpr)
        if kinds != len(played):
            main = next(r for r, k in zip(rects, kinds) if k is TileSize.MAIN)
            side = next(r for r, k in zip(rects, kinds) if k is TileSize.SIDE)
            shapes.add("below" if side.y >= main.y + main.h else "beside")
        else:
            main = rects[0]
            shapes.add("grid")
        assert need_w <= main.w and need_h <= main.h, (area, dpr, played, launch, main)
    assert shapes == {"beside", "below", "grid"}


# -- the fallbacks -----------------------------------------------------------------------------


def test_where_no_strip_fits_the_tiles_are_the_equal_grid_and_its_targets_hold():
    fell_back = 0
    for dpr, area, _, layout, played, launch in _every_placement():
        rects, kinds, targets = _placed(layout, played, area, launch, dpr)
        if kinds != len(played):
            continue
        assert rects == equal_grid(len(played), *area)
        assert targets == [launch] * len(played)
        fell_back += 1
    assert fell_back > 1_000


def test_pair_falls_back_where_its_main_tiles_would_be_smaller_than_the_grid_s():
    """Two Main tiles and a strip below would be 640x360 each, and the launch is 746x420."""
    area, launch = (1520, 980), (746, 420)
    assert gs.fits(launch, 3, area)

    rects, kinds, targets = _placed(PAIR, [True, True, False], area, launch)

    assert kinds == 3
    assert targets == [launch] * 3
    assert rects == equal_grid(3, *area)


def test_a_main_tile_at_the_smallest_rung_leaves_no_strip():
    """A Side tile would have no rung below it. The Main tile fits, beside, in 1100 wide."""
    assert not strip_fits(FOCUS, [True, False], (1100, 360), gs.rung_tiles(aspect_family(WIDE)))


# -- no Side tile, no resize -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("layout", "played"),
    [(FOCUS, [True]), (GRID, [True, True]), (GRID, [True, False]), (PAIR, [True, True]), (GRID, [True] * 4)],
)
@pytest.mark.parametrize("launch", [(534, 300), (1280, 720), (800, 600)])
def test_with_no_side_tile_every_target_is_the_launch_size(layout, played, launch):
    _, _, targets = _placed(layout, played, SHOWN_4K, launch)

    assert targets == [launch] * len(played)


def test_a_one_account_session_never_asks_for_a_resize_whatever_the_area():
    for area in AREAS:
        _, _, targets = _placed(FOCUS, [True], area, (640, 360))
        assert targets == [(640, 360)]


# -- a client with no ladder, and mixed sessions -------------------------------------------------


def test_a_client_with_no_ladder_has_no_target():
    no_ladder = (1280, 1024)
    tiles = [TileSize.MAIN, TileSize.SIDE]
    areas = [tile_size((1280, 720)), (622, 971)]

    assert tile_targets([no_ladder, WIDE], areas, tiles) == [None, (534, 300)]
    assert tile_targets([WIDE, no_ladder], areas, tiles) == [(1280, 720), None]
    assert tile_targets([no_ladder], [(2000, 2000)], 1) == [None]


def test_a_client_of_unknown_launch_has_no_target():
    assert tile_targets([None, WIDE], [(622, 971), (622, 971)], 2) == [None, WIDE]


def test_a_client_of_the_other_family_takes_its_own_family_s_rung():
    tiles = [TileSize.MAIN, TileSize.SIDE]
    areas = [tile_size((1280, 720)), (622, 971)]

    assert tile_targets([WIDE, NARROW], areas, tiles) == [(1280, 720), (560, 420)]


def test_a_side_rung_of_the_other_family_stays_below_main_by_pixels():
    """A tile that holds 800x600 beside a Main of 854x480, which has fewer pixels, so 720x540."""
    tiles = [TileSize.MAIN, TileSize.SIDE]
    areas = [tile_size((854, 480)), (2000, 2000)]

    assert tile_targets([WIDE, NARROW], areas, tiles) == [(854, 480), (720, 540)]


# -- with no rungs, as before --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("layout", "played", "area", "aspect", "expected"),
    [
        (FOCUS, [True, False], HIDDEN_1080, 16 / 9, [(0, 68, 1483, 834), (1489, 0, 431, 242)]),
        (
            FOCUS,
            [True, False, False, False],
            SHOWN_1080,
            4 / 3,
            [(0, 4, 1283, 962), (1289, 0, 323, 242), (1289, 248, 323, 242), (1289, 496, 323, 242)],
        ),
        (
            PAIR,
            [True, True, False],
            SHOWN_1440,
            16 / 9,
            [(0, 181, 1123, 631), (1129, 181, 1123, 631), (0, 999, 590, 332)],
        ),
        (
            GRID,
            [True] * 4,
            HIDDEN_1080,
            16 / 9,
            [(0, 0, 857, 482), (863, 0, 857, 482), (0, 488, 857, 482), (863, 488, 857, 482)],
        ),
        (FOCUS, [False, True], (1000, 1400), 4 / 3, [(0, 1150, 333, 250), (0, 197, 1000, 750)]),
    ],
)
def test_with_no_rungs_arrange_answers_as_it_did(layout, played, area, aspect, expected):
    """Written down from the code before rungs existed, as BartleBot's pane sizing tests are."""
    assert [(r.x, r.y, r.w, r.h) for r in arrange(layout, played, area, aspect=aspect)] == expected


def test_the_launch_fit_is_the_equal_grid_whichever_way_the_tiles_are_placed():
    for dpr in DISPLAY_SCALES:
        for resolution in VALID_RESOLUTIONS:
            tile_w, tile_h = tile_size(resolution, dpr)
            assert gs.required_game_area(resolution, 1, dpr) == (tile_w, tile_h)
            assert gs.required_game_area(resolution, 2, dpr) == (2 * tile_w, tile_h)
            assert gs.required_game_area(resolution, 4, dpr) == (2 * tile_w, 2 * tile_h)
