"""The (resolution, UI size) ladder, and whether a grid of clients fits the screen.

Wizard101's own options dropdown will not go below 800x600, but that is a UI
restriction rather than an engine limit: ``Resolution`` is a free-form ``WxH`` string
and the engine renders whatever is in it, verified down to 400x300. Two clients
at the stock floor do not fit side by side on a 1080p screen, which is the whole reason
this module exists.

WHY RESOLUTION AND UI SIZE ARE ONE SETTING

The HUD does not scale with the window -- shrink the resolution alone and the interface
stays at its original pixel size and overflows the client area. The compensation is the
game's own UI scale, which goes down to Micro, one step below anything the in-game menu
offers. So 400x300 *renders* perfectly well paired with Micro and is broken at the
default UI size, and the two must never be independently selectable.

A rendering HUD is not the same as a reachable one, though. The interact prompt is
centered and the compass and teleporter buttons are anchored to an edge, so on a narrow
client the prompt covers them -- and those buttons are clicked by coordinate, so a
covered one swallows the click. Narrowness therefore costs ladder steps off the render
floor: none at 16:10 or wider, one at 4:3, two below that. It is why 400x300 is not
offered at all -- it is 4:3, and Micro has no step to give.

The game does not clamp UI size against resolution itself: a UI too large for the client
area is reachable in-game and simply renders broken. That very likely explains the stock
800x600 floor -- it is the lowest resolution at which the *largest* UI size still works,
so the game never has to police the pairing. Going lower is not fighting the engine; it
is relaxing a conservative floor by controlling the pairing here instead.

WHERE THE TWO VALUES LIVE

Not guessable, and the reason for the comment in ``_patch_window_config``:

    Resolution  -> preferences.xml   (it appears in config.xml too, but that copy is
                                      only a mirror and is rewritten at launch)
    UISize      -> config.xml        (absent from preferences.xml entirely)

SCREEN SPACE CAPS THE GAME SIZE

The user must never have to scroll to see a game window, so the available screen space
sets a *maximum* game size and only resolutions at or below it may be launched. That
runs screen -> game size, the inverse of the ``minimum_window_size`` chain, which
derives a window minimum from a game size already chosen. Both are kept: once the
resolution is capped to fit the screen the minimum it implies is always satisfiable, so
that chain stops being able to refuse to open and does its real job -- keeping the
window and the splitters from being dragged down to where the game gets clipped.

Nothing here is a QWidget or reads settings, so the arithmetic can be tested on its own.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import NamedTuple

from bartleby.runtime.game.tile_layouts import GAP, SIDE_SHARE, TileSize

# ---------------------------------------------------------------------------
# Tile chrome
# ---------------------------------------------------------------------------
# These live here rather than in client_tile.py because they are as much a term in the
# fit arithmetic as they are a drawing constant, and this module must not import a
# QWidget to know how big a tile is. client_tile.py imports them back.

FOCUS_BORDER = 6  # logical pixels of accent fill around the game window when focused
LABEL_GAP = 3  # logical pixels between the bottom of the pill and the top of the focus border
LABEL_PILL_H = 26  # conservative upper bound on pill height (max(text_h, ICON_SIZE) + 6)
LABEL_RESERVE = FOCUS_BORDER + LABEL_GAP + LABEL_PILL_H + 4  # = 39; keeps the pill clear of the border

# The title bar: the window's one strip above the panes, holding the logo, the menus, the
# layout toggles and the window buttons. The client area reaches the top of the window
# (bartleby/qt/title_bar), so there is no native caption above it to count -- this is all of it.
TITLE_BAR_PX = 35

# The rule along the window's bottom (bartleby/qt/title_bar/bottom_edge.py), as thick as the
# splitter handles the theme draws between panes. Shown only where Windows draws no
# border there, but counted always: a floor a pixel too high costs nothing, and one too
# low is a floor the window has and the arithmetic does not.
BOTTOM_EDGE_PX = 2

# What screen_budget takes off the screen's height for everything that is neither the game
# area nor a pane below it. An estimate rather than a sum: it was the menu bar plus a status
# bar once, and the title bar that replaced both is smaller, but moving it re-tiers the
# offered ladder. The live game area is exact, and picks up the room the title bar freed.
WINDOW_CHROME_ESTIMATE_PX = 60

# The game panel's own header strip, above the view the tiles sit in.
GAME_PANEL_CHROME_PX = 32


# ---------------------------------------------------------------------------
# The panes and their floors
# ---------------------------------------------------------------------------
# A product's window is the game area with panes beside and below it: BartleBot's two
# sidebars and its bot canvas, BartleBuddy's one sidebar. Each product lists its own as
# ``Pane``s, and the arithmetic below reads the list, never a product's constants.
#
# A pane's floor is what it may be dragged down to, and it is a term in the arithmetic,
# not a suggestion: a pane whose *enforced* minimum is larger than the floor it lists makes
# ``required_window_size`` understate the floor Qt will hold the window to, and the window
# then cannot be given the size the fit was computed for -- which is how a 2560x1440
# launch cleared the fit and then could not open on a 4K screen.
#
# Qt takes the larger of a widget's ``minimumSize`` and its layout's minimum, *except*
# that an explicitly set ``minimumSize`` wins outright (``qSmartMinSize``). So each pane
# sets its floor on itself and its content is clipped -- or scrolls, where a scroll area is
# already in the way -- rather than being allowed to raise the window's floor.
# BartleBot's ``tests/gui/test_window_minimum.py`` is the guard.
#
# Floors are *measured* where a pane's widest row is a set of controls that carry a
# ``Minimum``/``Preferred`` size policy with no shrink flag: they run off the pane rather
# than getting narrower, and their own width scales with the system text size and the
# resolved style. A floor is **the forcing row plus the chrome around it**, and only the row
# is measured. Both of BartleBot's floors used to be the whole reference width scaled by a
# ratio, which charged the font's growth against the scroll bar, the frame and the margins
# as well, none of which a font moves; on a 1080p screen that took away three- and
# four-client launches outright. Measure the row, add the chrome. ``right_sidebar_floor``
# below is the one floor both products share; BartleBot's own (``game/pane_sizing.py``)
# hold the rest.

LEFT = "left"
RIGHT = "right"
BOTTOM = "bottom"


class Pane(NamedTuple):
    """One pane beside or below the game area: its name, its floor, and which side it is on.

    *name* is the key the product keeps the pane's size under (BartleBot's
    ``left_sidebar_width``), so the sizes the arithmetic is handed are looked up by it.
    *floor* is in logical pixels, a width for a ``LEFT`` or ``RIGHT`` pane and a height for
    a ``BOTTOM`` one.
    """

    name: str
    floor: int
    side: str

    @property
    def beside(self) -> bool:
        """Whether the pane takes width from the game area, rather than height."""
        return self.side in (LEFT, RIGHT)


RIGHT_SIDEBAR_MIN_WIDTH = 300
# The right sidebar's forcing row is the Log tab's header: the "Min level:" caption, the
# level combo, Export and Clear. Both products' right sidebars hold the Log tab, so this
# floor is the family's. The tool row this floor was first measured from is gone -- the
# player card carries icon verbs and elided rows now -- and a button's width was standing
# in for a row that no longer existed. ``log_panel.header_row_floor`` measures this one,
# and it is mostly pinned widths, so it barely moves with the font: 281px at the
# reference, 289 at 14px body text.
REFERENCE_LOG_ROW_PX = 281
# What the sidebar spends *around* its forcing row -- margins, frames, the scroll bar, the
# gutter. Style metrics and fixed spacings, not text, so they are added rather than
# scaled: the difference between the reference floor and its reference row, which keeps
# the floor exactly at the reference at the reference metrics.
RIGHT_SIDEBAR_CHROME_PX = RIGHT_SIDEBAR_MIN_WIDTH - REFERENCE_LOG_ROW_PX  # 19
# Shared: the height must stay under the shortest tile any offered resolution produces,
# or a sidebar rather than the game view becomes the top row's floor.
SIDEBAR_MIN_HEIGHT = 240
# Splitter handle allowance: one per pane, between it and the game area
SPLITTER_HANDLE_PX = 8


# ---------------------------------------------------------------------------
# The ladder
# ---------------------------------------------------------------------------


class UiSize(NamedTuple):
    """One rung: what the game calls this scale, what it stores, and its render floor.

    *min_resolution* is the smallest client area whose HUD at this scale still *renders*
    -- established by driving every control at each pairing in a live client. Below it the
    interface overflows the client area outright.

    That floor is not the whole answer, because a HUD that draws is not necessarily one
    that can be clicked; see ``ASPECT_BAND_STEPS`` for the second half.
    """

    name: str
    value: int
    min_resolution: tuple[int, int]


# In ascending *visual* order, which is the only order anything here may use. The stored
# values are not ordered by size: Micro was appended after the original five and got the
# next free number. Nothing may sort or compare these numerically.
UI_SIZE_LADDER: tuple[UiSize, ...] = (
    UiSize("Micro", 5, (400, 300)),
    UiSize("Tiny", 0, (480, 360)),
    UiSize("Small", 1, (560, 420)),
    UiSize("Regular", 2, (640, 480)),
    UiSize("Large", 3, (720, 540)),
    UiSize("Huge", 4, (800, 600)),
)


# ---------------------------------------------------------------------------
# What narrowness costs
# ---------------------------------------------------------------------------
# The second floor, and the one that is not about rendering at all. The HUD anchors the
# compass and teleporter buttons to a screen edge and centers the interact prompt, so on a
# narrow client the prompt covers them -- and those buttons are clicked by coordinate
# (node_helpers.click_by_path -> wizwalker click_window), so a covered one swallows the
# click and the node runs out its timeout instead of failing loudly.
#
# The cost is measured in ladder steps off the render floor, and it depends on the aspect
# ratio *alone*. Not on size: 2048x1536 is 4:3 with 1536 lines of height and still will not
# take Huge, which rules out any model where a big enough client outgrows the problem. Ten
# pairings were driven in a live client on a sigil, and this reproduces all ten:
#
#     >= 16:10   0 steps   1128x634 Huge, 1680x1050 Huge, and every 16:9 rung
#     >= 4:3     1 step    480x360 Micro, 560x420 Tiny, 640x480 Small, 720x540 Regular,
#                          800x600 Large, 1024x768 Large, 1600x1200 Large, 2048x1536 Large
#     narrower   2 steps   1280x1024 Regular
#
# One step off the render floor is also, exactly, the next rung's render floor -- which is
# why this was first written as a per-rung "narrow floor" column and read as data. It was
# only ever one number wide, and the single entry that column could not derive was the one
# that was wrong: Huge was given a narrow floor of 1280x960, and 1600x1200 and 2048x1536
# both say no 4:3 size takes Huge at any size at all. There is no rung above Huge to step
# down from, which is the whole of it.
#
# Bands are held as integers and compared by cross-multiplication so a ratio sitting
# exactly on a boundary cannot be decided either way by a float. Ordered widest first.
#
# 16:10 is measured, not a judgement: 1680x1050 is 8:5 and takes Huge. Do not tighten it
# toward exact 16:9 -- ``widescreen_form``'s even-pixel rounding lands some rungs
# fractionally *below* 16:9 (746x420 is 1.7762), so they would misclassify as narrow. 7:4
# is the tightest safe boundary if this ever has to move.
ASPECT_BAND_STEPS: tuple[tuple[tuple[int, int], int], ...] = (
    ((16, 10), 0),
    ((4, 3), 1),
)

# What anything narrower than the last band costs. 5:4 is the only such ratio offered.
NARROW_BAND_STEPS = 2

# Retained as the name the rest of the code asks by; it is simply the first band.
WIDESCREEN_RATIO: tuple[int, int] = ASPECT_BAND_STEPS[0][0]


def scale_steps(resolution: tuple[int, int]) -> int:
    """How many ladder steps below its render floor *resolution* has to run.

    Zero for anything 16:10 or wider, where the centered prompt clears the edge-anchored
    buttons on its own and the render floor is the only thing that binds.
    """
    width, height = resolution
    for (num, den), steps in ASPECT_BAND_STEPS:
        if width * den >= height * num:
            return steps
    return NARROW_BAND_STEPS


def is_widescreen(resolution: tuple[int, int]) -> bool:
    """Whether *resolution* is wide enough for the centered prompt to clear the HUD edges."""
    return scale_steps(resolution) == 0


def widescreen_form(resolution: tuple[int, int]) -> tuple[int, int]:
    """The 16:9 form of *resolution*: the same height, widened to a 16:9 width.

    Widening rather than shortening is what lets this be offered without re-driving every
    control in a live client. A HUD cannot be broken by being handed more room on one axis
    and the same on the other, so the 16:9 form of a rung inherits the verification of that
    rung's render floor -- and, by moving the edge-anchored buttons away from the centered
    prompt, pays no narrowness steps rather than having to clear a higher floor. Holding
    the *width* and deriving the height instead (400x300 -> 400x225) would drop below the
    render floor, which is the broken pairing the ladder exists to prevent.

    The width is rounded to an even pixel. That is not only the convention every stock 16:9
    size in ``VALID_RESOLUTIONS`` follows -- 16:9 is not integral at these heights -- it is
    the game's own: fed the heights from its dropdown this returns its own entries exactly,
    634 -> 1128x634 and 990 -> 1760x990.
    """
    _, height = resolution
    return round(height * 16 / 9 / 2) * 2, height


# Every offered resolution below the stock floor: each ladder rung's 16:9 form, and the 4:3
# floors from Tiny up. Discrete rather than freeform -- every offered resolution maps to
# exactly one maximum UI size, which is what keeps the pairing testable.
#
# Micro's own 4:3 floor, 400x300, is deliberately not in here. It renders fine, but 4:3
# costs a ladder step and there is no scale below Micro to spend it, so no pairing at
# 400x300 works at all -- ``has_unoccluded_ui_size`` is the guard. The only way out at that
# height is width, and that is 534x300.
SUB_FLOOR_RESOLUTIONS: list[tuple[int, int]] = sorted(
    {rung.min_resolution for rung in UI_SIZE_LADDER[1:-1]}
    | {widescreen_form(rung.min_resolution) for rung in UI_SIZE_LADDER[:-1]}
)

# Resolutions from the game's own dropdown, at and above its 800x600 floor. A subset of
# it: the dropdown also offers 1128x634, 1760x990, 3072x1728 and 3200x1800, which are
# real 16:9 sizes but match no monitor anyone runs, so they are left out rather than
# padding every picker with sizes nobody picks.
#
# Nothing here is off the dropdown. 1280x800 and 1280x960 used to be, and were removed
# when the aspect bands were measured: neither is a size the game offers, 1280x960 was
# the resolution the mistaken Huge narrow floor was built on, and 1280x800 existed mainly
# as the one entry that sat exactly on the 16:10 boundary. 1680x1050 is 8:5 as well and
# is measured, so the boundary keeps a live case.
_STOCK_RESOLUTIONS: list[tuple[int, int]] = [
    (800, 600),
    (1024, 768),
    (1280, 720),
    (1280, 1024),
    (1366, 768),
    (1600, 900),
    (1600, 1200),
    (1680, 1050),
    (1920, 1080),
    (1920, 1200),
    (2048, 1536),
    (2560, 1440),
    (3840, 2160),
]

# Valid Wizard101 windowed resolutions (width, height), smallest first.
#
# The sub-floor ones are the game_sizing ladder: the game's own dropdown stops at
# 800x600, but that is a UI restriction rather than an engine limit, and each of them
# pairs with exactly one UI scale. They are what makes 2-4 embedded clients fit a
# 1080p screen. The smallest offered is 534x300; 400x300 renders but leaves the
# teleporter buttons under the interact prompt with no smaller UI scale to escape to,
# so game_sizing.has_unoccluded_ui_size rejects it and a test holds the whole list to
# that bar.
#
# Sorted rather than concatenated because the two lists interleave: a rung's 16:9 form is
# wider than its 4:3 one, so 854x480 and 960x540 both sort above 800x600 while still
# being sub-floor sizes in the sense that matters -- they take a UI scale below Huge.
#
# "Sub-floor" is about the scale, not the size: the 4:3 entries at and above 800x600 top
# out at Large too, because no 4:3 resolution reaches Huge at any size.
VALID_RESOLUTIONS: list[tuple[int, int]] = sorted({*SUB_FLOOR_RESOLUTIONS, *_STOCK_RESOLUTIONS})


def _render_floor_index(resolution: tuple[int, int]) -> int | None:
    """The highest rung *resolution* can render, or None if it is under every floor."""
    width, height = resolution
    best: int | None = None
    for index, rung in enumerate(UI_SIZE_LADDER):
        if width >= rung.min_resolution[0] and height >= rung.min_resolution[1]:
            best = index
    return best


def _reachable_index(resolution: tuple[int, int]) -> int | None:
    """The highest rung *resolution* can both draw and be clicked at, or None if there is none."""
    floor = _render_floor_index(resolution)
    if floor is None:
        return None
    index = floor - scale_steps(resolution)
    return index if index >= 0 else None


def max_ui_size(resolution: tuple[int, int]) -> UiSize:
    """The largest UI scale *resolution* can carry with the HUD both drawn and reachable.

    The render floor sets the ceiling and the aspect ratio takes steps off it, so a 4:3
    client runs one scale below what it could draw and 1024x768 takes Large rather than
    Huge. No 4:3 size takes Huge, at any size.

    Always returns a rung, including for a resolution that has no working pairing -- there
    is no scale below Micro to fall back to, so Micro is what is left. That case is not
    reachable from the offered list: ``has_unoccluded_ui_size`` is what keeps such a
    resolution out of ``VALID_RESOLUTIONS``.
    """
    index = _reachable_index(resolution)
    return UI_SIZE_LADDER[index] if index is not None else UI_SIZE_LADDER[0]


def has_unoccluded_ui_size(resolution: tuple[int, int]) -> bool:
    """Whether *any* UI scale works at *resolution*, prompt occlusion included.

    False is precisely why 400x300 is not offered -- 4:3 costs a step and Micro has none to
    give -- and it is the check a new entry in ``VALID_RESOLUTIONS`` has to pass.
    ``max_ui_size`` still answers Micro for such a resolution because it has to answer
    something; this is the question that tells the two cases apart.
    """
    return _reachable_index(resolution) is not None


# The stock 800x600 floor can render Large (see MEASURED_PAIRINGS), but that ceiling is
# about what the HUD can draw and click, not about what a user arriving at the game's own
# default resolution should be bumped up to. ui_size_for caps this one resolution at
# Regular rather than the render-floor ceiling, so a preference larger than Regular gets
# pulled down at 800x600 specifically instead of riding up to Large.
_DEFAULT_RESOLUTION_UI_SIZE_CAP: dict[tuple[int, int], str] = {(800, 600): "Regular"}


def ui_size_for(preferred_value: int | None, resolution: tuple[int, int]) -> int | None:
    """The ``UISize`` to write for *resolution*, given the user's own preference.

    Clamps **down** only -- a resolution that can carry the user's preferred scale gets
    it untouched, and one that cannot gets the largest that fits. Never raises it: a
    user who plays at Small does not want Huge because their screen would allow it.

    *preferred_value* is the user's setting, not whatever is in config.xml right now,
    which after one clamped session is our own value. None (nothing has captured it)
    returns None, meaning leave the file alone: with no preference to clamp there is
    also nothing to restore, and writing a value we never read would silently replace a
    setting we would then have no way to give back.

    Returning the preferred value unchanged is load-bearing rather than a no-op. It is
    what puts the scale *back* when a later launch picks a resolution that can carry it
    -- otherwise one launch at 400x300 leaves Micro behind for every launch after it.
    """
    if preferred_value is None:
        return None
    order = {rung.value: index for index, rung in enumerate(UI_SIZE_LADDER)}
    allowed_name = _DEFAULT_RESOLUTION_UI_SIZE_CAP.get(resolution)
    allowed_value = next(r.value for r in UI_SIZE_LADDER if r.name == allowed_name) if allowed_name else None
    allowed = max_ui_size(resolution)
    allowed_index = order[allowed_value] if allowed_value is not None else order[allowed.value]
    preferred_index = order.get(preferred_value)
    if preferred_index is None:
        # A value outside the enum: the file is not saying anything that can be honored,
        # so take the ceiling rather than pass an unknown number back to the game.
        return allowed_value if allowed_value is not None else allowed.value
    if preferred_index <= allowed_index:
        return preferred_value
    return allowed_value if allowed_value is not None else allowed.value


# ---------------------------------------------------------------------------
# Fit
# ---------------------------------------------------------------------------


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


def tile_size(resolution: tuple[int, int], dpr: float = 1.0) -> tuple[int, int]:
    """The logical pixels one tile occupies: the game window plus its chrome.

    The game area is divided by *dpr* because a resolution is physical pixels while the
    border and the label strip are logical ones -- on a 150% display a 400px-wide game
    window is 267 logical pixels of tile.
    """
    width, height = resolution
    dpr = dpr or 1.0
    return int(width / dpr) + 2 * FOCUS_BORDER, int(height / dpr) + LABEL_RESERVE + 2 * FOCUS_BORDER


#: What a grid is asked about: a count of equal tiles, or each tile's size in slot order.
#: A count is BartleBot's one equal grid; a list may hold Side tiles (``tile_layouts``).
Tiles = int | Sequence[TileSize]


def tile_sizes(tiles: Tiles) -> list[TileSize]:
    """*tiles* as a size per tile: a count is that many Main tiles, an equal grid."""
    if isinstance(tiles, int):
        return [TileSize.MAIN] * max(0, tiles)
    return list(tiles)


def required_game_area(resolution: tuple[int, int], tiles: Tiles, dpr: float = 1.0) -> tuple[int, int]:
    """The logical pixels a grid of *tiles* at *resolution* needs.

    Main tiles alone are an equal grid (``grid_for``), each a whole ``tile_size``. With
    Side tiles too, the area is the one ``tile_layouts.arrange`` places them in with the
    strip beside the Main tiles: the Main tiles in a row, each a rectangle of the game's
    aspect that holds a whole ``tile_size``, since ``arrange`` fits every tile to the
    aspect; and the strip, a column of Side tiles ``SIDE_SHARE`` of that height tall. A
    Side tile has no size of its own to need: until a running client can be resized, it
    shows its game at the launch size, centered and cut to the tile.
    """
    sizes = tile_sizes(tiles)
    mains = sizes.count(TileSize.MAIN)
    tile_w, tile_h = tile_size(resolution, dpr)
    if mains == len(sizes):
        rows, cols = grid_for(mains)
        return cols * tile_w, rows * tile_h
    aspect = resolution[0] / resolution[1]
    main_w = max(tile_w, math.ceil(tile_h * aspect))
    main_h = math.ceil(main_w / aspect)
    strip_w = math.ceil(main_h * SIDE_SHARE * aspect)
    return mains * main_w + GAP * mains + strip_w, main_h


def right_sidebar_floor(log_row_px: float | None = None) -> int:
    """How wide the right sidebar has to be for the widest row it cannot shrink.

    *log_row_px* is the Log tab's header row, measured at this machine's font and style
    by ``bartleby.qt.sidebar.log_panel.header_row_floor``: the caption, the level combo,
    Export and Clear, none of which wrap or elide. ``RIGHT_SIDEBAR_CHROME_PX`` is everything
    around it, added rather than scaled because a font does not widen a frame, a margin
    or a scroll bar.

    The row carries both the font *and* the style, which is what the old two-term scale
    was reaching for: a ``QPushButton`` is never narrower than the style's own minimum --
    75px under ``windowsvista``, 80 under Fusion, 81 under ``windows11`` -- and measuring
    a row built from real widgets picks that up wherever it lands, including on a style
    no Windows version here has yet.

    Never below the reference: a smaller font does not make the tab bar or the scroll bar
    narrower, and a floor that tracked one downwards would clip the panel it protects.

    ``None`` means "not measured" and returns the reference, which is what the pure
    arithmetic and its tests use. Everything inside a running app must instead go
    through ``bartleby.qt.screen_fit.right_sidebar_floor``, so that the width the widget
    enforces and the width the fit is computed against cannot come from two different
    measurements.
    """
    if log_row_px is None:
        return RIGHT_SIDEBAR_MIN_WIDTH
    return max(RIGHT_SIDEBAR_MIN_WIDTH, round(log_row_px + RIGHT_SIDEBAR_CHROME_PX))


def required_window_size(
    resolution: tuple[int, int],
    tiles: Tiles,
    panes: Sequence[Pane],
    dpr: float = 1.0,
) -> tuple[int, int]:
    """The window floor a grid of *tiles* at *resolution* implies, beside *panes*.

    The inverse of ``screen_budget``: that one runs screen -> game size to decide what may
    be launched, this one runs game size -> window floor once a size has been chosen. It
    is the size the window may be *dragged* to, so it is measured against each pane's
    floor rather than the user's configured layout -- unlike ``screen_budget``, which must
    not assume the user will crush their panes.

    Every term the window actually contains is here: each pane's floor and its splitter
    handle, the title bar, the bottom edge and the game panel's header. The tile chrome
    and the game panel's header used to be left out, which made the declared floor
    smaller than the one Qt enforces -- and a declared floor that is not the real one is
    worse than none, because it is the number the rest of the app reasons about.
    """
    game_w, game_h = required_game_area(resolution, tiles, dpr)
    beside = [pane for pane in panes if pane.beside]
    below = [pane for pane in panes if not pane.beside]
    width = game_w + sum(pane.floor for pane in beside) + SPLITTER_HANDLE_PX * len(beside)
    height = (
        game_h
        + GAME_PANEL_CHROME_PX
        + sum(pane.floor for pane in below)
        + SPLITTER_HANDLE_PX * len(below)
        + TITLE_BAR_PX
        + BOTTOM_EDGE_PX
    )
    return width, height


def screen_budget(
    screen_size: tuple[int, int],
    panes: Sequence[Pane],
    sizes: Mapping[str, int],
) -> tuple[int, int]:
    """What is left of *screen_size* for the game grid once *panes* are placed at *sizes*.

    *sizes* holds each pane's size by its name, as the user configured it; a pane missing
    from it is taken at its floor. Measured against the configured sizes, never the floors
    those panes can be dragged down to. Sizing against the floors assumes the user wants
    their panes crushed to make room for a bigger game window, which nobody does -- it
    would offer a resolution that only fits as long as they never touch a splitter.

    A pane beside the game area costs its width and a splitter handle. One below costs
    its height, and its handle is inside ``WINDOW_CHROME_ESTIMATE_PX``.
    """
    screen_w, screen_h = screen_size
    beside = [pane for pane in panes if pane.beside]
    width = screen_w - sum(sizes.get(pane.name, pane.floor) for pane in beside) - SPLITTER_HANDLE_PX * len(beside)
    below = sum(sizes.get(pane.name, pane.floor) for pane in panes if not pane.beside)
    height = screen_h - below - WINDOW_CHROME_ESTIMATE_PX
    return max(0, width), max(0, height)


def layout_budget(
    screen_size: tuple[int, int],
    panes: Sequence[Pane],
    sizes: Mapping[str, int],
) -> tuple[int, int]:
    """``screen_budget``, with each pane held at its floor.

    The cap that decides which resolutions may be launched. A stored size below a pane's
    floor describes a layout the app cannot produce, so budgeting against it hands the
    game area space the splitter will not give it. The clamp is on the read rather than
    the stored value, which stays the user's.
    """
    held = {pane.name: max(sizes.get(pane.name, pane.floor), pane.floor) for pane in panes}
    return screen_budget(screen_size, panes, held)


def fits(resolution: tuple[int, int], tiles: Tiles, budget: tuple[int, int], dpr: float = 1.0) -> bool:
    """Whether *tiles* at *resolution* fit inside *budget*."""
    need_w, need_h = required_game_area(resolution, tiles, dpr)
    budget_w, budget_h = budget
    return need_w <= budget_w and need_h <= budget_h


def largest_fitting(
    resolutions: list[tuple[int, int]],
    tiles: Tiles,
    budget: tuple[int, int],
    dpr: float = 1.0,
) -> tuple[int, int] | None:
    """The biggest of *resolutions* that fits, or None if not even the smallest does.

    "Biggest" is by area, so a wide 16:9 tier and a tall 4:3 one are ranked by how much
    game they actually show rather than by whichever axis is listed first.
    """
    fitting = [r for r in resolutions if fits(r, tiles, budget, dpr)]
    if not fitting:
        return None
    return max(fitting, key=lambda r: (r[0] * r[1], r))


class FitProblem(NamedTuple):
    """Why a launch will not fit, in the pieces a surface needs separately.

    One sentence was enough while the only surface was a tooltip. It is not enough for
    the launch screen, where every account card asks the same question -- 1 client at the
    app resolution, on the one screen -- and so gets back the same paragraph, wrapped
    three lines deep, once per card. The parts differ in how they repeat:

        ``numbers``  belongs to the card. Two profiles bringing different client counts
                     genuinely need different amounts of room.
        ``remedy``   belongs to the column. It is the same advice for every entry that
                     does not fit, because the three ways out are properties of the
                     layout rather than of any one profile.

    ``sentence`` is still both, and is still what a tooltip and a disabled menu entry
    carry -- those are read one at a time, so there is nothing there to repeat.

    Sizes are logical pixels scaled back to the units the *resolution* is quoted in, so
    all three numbers in a sentence are comparable. See ``fit_details``.
    """

    resolution: tuple[int, int]
    client_count: int
    need: tuple[int, int]
    available: tuple[int, int]

    @property
    def clients(self) -> str:
        return f"{self.client_count} client{'s' if self.client_count != 1 else ''}"

    @property
    def numbers(self) -> str:
        """The card's line: what this entry needs, and what there is. No advice."""
        return f"Needs {self.need[0]}×{self.need[1]} · {self.available[0]}×{self.available[1]} available"

    @property
    def remedy(self) -> str:
        """The advice, which is the same for every entry that does not fit.

        Three ways out, and the third is not a screen-space one: the budget is the larger
        of the configured layout and the live game area (see ``bartleby.qt.screen_fit.budget_for``),
        so dragging a sidebar narrower widens it there and then.
        """
        return "Lower the resolution, launch fewer clients, or make the game area larger."

    @property
    def sentence(self) -> str:
        """Both halves, for a surface that shows one of these at a time."""
        return (
            f"{self.clients} at {self.resolution[0]}×{self.resolution[1]} "
            f"needs {self.need[0]}×{self.need[1]}; "
            f"there is {self.available[0]}×{self.available[1]} available — "
            f"{self.remedy[0].lower()}{self.remedy[1:]}"
        )


def fit_details(
    resolution: tuple[int, int],
    tiles: Tiles,
    budget: tuple[int, int],
    dpr: float = 1.0,
) -> FitProblem | None:
    """Why *tiles* at *resolution* will not fit, or None if they will.

    An entry that cannot launch is shown disabled carrying this rather than hidden: a
    profile that has vanished leaves the user hunting for it with no indication of why,
    which is worse than a grayed row that says what would bring it back.
    """
    if fits(resolution, tiles, budget, dpr):
        return None
    need_w, need_h = required_game_area(resolution, tiles, dpr)
    budget_w, budget_h = budget
    # Both numbers are logical pixels, and the resolution beside them is physical ones, so
    # on a scaled display the sentence would otherwise read as a client needing *less*
    # space than its own resolution -- 2048x1536 "needs 1650x1279" at 125%. The comparison
    # stays logical; only what is reported is converted, so all three numbers are in the
    # units the user picked the resolution in. dpr 1.0 leaves them untouched.
    scale = dpr or 1.0
    return FitProblem(
        resolution=resolution,
        client_count=len(tile_sizes(tiles)),
        need=(round(need_w * scale), round(need_h * scale)),
        available=(round(budget_w * scale), round(budget_h * scale)),
    )


def fit_problem(
    resolution: tuple[int, int],
    tiles: Tiles,
    budget: tuple[int, int],
    dpr: float = 1.0,
) -> str | None:
    """``fit_details`` as one sentence, for tooltips and disabled menu entries."""
    problem = fit_details(resolution, tiles, budget, dpr)
    return None if problem is None else problem.sentence
