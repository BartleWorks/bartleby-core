"""Whether a launch will fit on screen, asked from a widget.

Moved from BartleBot's ``gui/screen_fit.py`` (Stage 9 of BartleBot #167). What it read of
BartleBot's settings and window is handed in by :func:`configure`: a ``FitSettings`` for
the resolution and the size the user gave each pane, and the product's panes, each with
its floor as measured here. BartleBot's own pane floors and the layout its setup fits are
BartleBot's ``gui/window_panes.py``.

``bartleby/runtime/game/game_sizing.py`` owns the arithmetic and knows nothing about Qt or settings.
This is the thin layer that supplies it with the things only the running app has: which
screen the window is on, that screen's scale factor, how wide the user has configured
the panes beside the game, and — where a caller can see it — how big the game area actually is right
now. ``budget_for`` explains why the last of those is needed and why it is a *maximum*
taken with the others rather than a replacement for them.

WHY THE ANSWER IS A REFUSAL RATHER THAN A WARNING

A game window is embedded at 1:1 and cannot scale, and the user must never have to
scroll to see one, so a grid that does not fit is not a degraded layout -- it is a
clipped one. Warning and proceeding, auto-detaching the overflow, or relaunching the
whole fleet smaller were all considered and dropped in favour of making the unfittable
state unreachable: nothing has to degrade gracefully into a state that cannot happen.

Both launch surfaces ask the same question against the *resulting* client count, so
adding a third client to a fitting pair is refused when the 2x2 grid it forces would not
fit at the resolution those two are already running at.

The entry that cannot launch stays visible and disabled carrying its reason. Hiding it
leaves the user hunting for a profile that has vanished with no indication why, which is
strictly worse than a grayed row that says what would bring it back.

That reason comes in two halves — see ``game_sizing.FitProblem``. A surface showing many
entries at once puts the numbers on each and the advice once for the lot; a surface
showing one at a time takes the whole sentence.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Protocol

from PyQt6.QtWidgets import QApplication, QWidget

from bartleby.runtime.game import game_sizing
from bartleby.runtime.game.game_sizing import VALID_RESOLUTIONS, Pane, Tiles


class FitSettings(Protocol):
    """What the fit reads of a product's settings."""

    @property
    def game_resolution(self) -> tuple[int, int]:
        """The resolution a launch that names none opens at."""
        ...

    def pane_sizes(self) -> Mapping[str, int]:
        """Each pane's size as the user set it, by the pane's name; a pane left out is at its floor."""
        ...


_settings: FitSettings | None = None
_panes: Callable[[], Sequence[Pane]] = list


def configure(settings: FitSettings, panes: Callable[[], Sequence[Pane]]) -> None:
    """Hand the fit the product's settings, and what lists its panes with their floors measured.

    *panes* is asked each time a budget is taken, since a floor follows the font and the
    style, and a product may hide a pane. Called once, before the first fit is asked.
    """
    global _settings, _panes
    _settings = settings
    _panes = panes


def settings() -> FitSettings:
    """The settings :func:`configure` was given."""
    if _settings is None:
        raise RuntimeError("screen_fit.configure() has not been called")
    return _settings


def panes() -> list[Pane]:
    """The product's panes, as the function :func:`configure` was given lists them now."""
    return list(_panes())


# Stands in for a screen that cannot be measured. Big enough that nothing is refused:
# an app that cannot see a screen must not be one that refuses to launch anything.
UNMEASURABLE = (1 << 30, 1 << 30)


def screen_metrics(widget: QWidget | None = None) -> tuple[tuple[int, int], float]:
    """The available size and scale factor of the screen *widget* is on.

    ``availableGeometry`` rather than the full geometry, so the taskbar is not counted as
    space the window could use. A widget that is not on a screen yet — a card built
    before its window is shown — falls back to the primary one, which is where an
    un-positioned window opens anyway.
    """
    screen = widget.screen() if widget is not None else None
    if screen is None:
        screen = QApplication.primaryScreen()
    if screen is None:
        # No QScreen at all: headless, or between screen changes. An unmeasurable screen
        # must not be reported as a screen nothing fits on, so nothing is refused.
        return UNMEASURABLE, 1.0
    available = screen.availableGeometry()
    return (available.width(), available.height()), screen.devicePixelRatio() or 1.0


#: The sidebar floors measured so far, by which side and what they were measured under.
_FLOORS: dict[tuple, int] = {}
#: More keys than a session ever produces -- one per style and theme it has run under. A
#: test suite sweeping styles can produce more, so past this the cache starts over.
_FLOORS_KEPT = 32


def measured_floor(side: str, app: QApplication, measure: Callable[[], int]) -> int:
    """*measure*'s answer for *side*, measured once per font, style and stylesheet.

    Public so a product measuring a floor of its own (BartleBot's left sidebar) shares the
    cache and its reasoning.

    A floor is a probe row of real widgets built and sized from scratch, and it only
    changes when one of those three does: they are what the row's width comes from. But it
    is asked for on every resize, once per launch card -- 14 times when a maximized window
    was dragged back to its normal size, which spent 70 of that resize's ~110ms building
    probe rows, and froze the window under the cursor while it did.
    """
    key = (side, app.font().key(), app.style().name(), app.styleSheet())
    floor = _FLOORS.get(key)
    if floor is None:
        if len(_FLOORS) >= _FLOORS_KEPT:
            _FLOORS.clear()
        floor = _FLOORS[key] = measure()
    return floor


def right_sidebar_floor() -> int:
    """How wide the right sidebar has to be *on this machine* for its widest row to fit.

    Always measured from the application font, never from a particular widget's, because
    two numbers derived here have to be the same number: the width a product's right
    sidebar enforces (BartleBot's ``RightSidebar``), and the width its right ``Pane`` lists
    before the budget decides what may launch.
    A floor the arithmetic does not know about is the whole failure this guards against
    -- see "The floor is only a floor if the panes honor it" in bartleby-core/docs/WINDOW_SIZING.md.

    Measured rather than a constant because the row is a caption beside controls, and
    both the font and the resolved style move it: the same layout wants 281px here and
    more on a machine with larger system text or a wider button style.

    The row measured is the Log tab's header — ``log_panel.header_row_floor``, built from
    the panel's own widgets so the two cannot drift. It is the widest thing in the panel
    that neither elides nor wraps, which the player card's tool row used to be: that row
    is gone, and until this was rewritten the floor was still a button's width scaled
    over the whole panel, charging a font's growth against content that had not moved.

    The probe is parentless and never shown. "Never shown" is load-bearing, not
    incidental — a parentless QWidget *is* a top-level window, so anything that made it
    visible (a setVisible, a show(), a style that needs it realised) would put a
    row-sized window on the user's screen for as long as this function runs. That is
    exactly the flash the launch cards had: see _make_quiet_label in
    bartleby/qt/game_embed/launch_cards.py. ``minimumSizeHint()`` alone does not realise it —
    verified: WA_WState_Created stays False, with and without the app stylesheet. If the
    probe ever has to be shown or realised, give it a parent.
    """
    app = QApplication.instance()
    if app is None:
        return game_sizing.RIGHT_SIDEBAR_MIN_WIDTH
    from bartleby.qt.sidebar.log_panel import header_row_floor  # noqa: PLC0415 - import cycle at module scope

    return measured_floor("right", app, lambda: game_sizing.right_sidebar_floor(header_row_floor(app.font())))


# Left clear of the screen edges when a window is fitted to it, so the frame and the
# taskbar are never what a dialog's buttons end up behind.
WINDOW_SCREEN_MARGIN = 40


def size_within_screen(
    preferred: tuple[int, int],
    minimum: tuple[int, int],
    available: tuple[int, int],
    margin: int = WINDOW_SCREEN_MARGIN,
) -> tuple[int, int]:
    """*preferred*, shrunk to fit *available* less *margin*, but never below *minimum*.

    The minimum wins over the screen on purpose. A window squeezed under its own minimum
    is one Qt will hand back a size it cannot honor anyway, and clamping past it is a
    second way to lose the buttons rather than a fix for the first.
    """
    return (
        max(minimum[0], min(preferred[0], available[0] - margin)),
        max(minimum[1], min(preferred[1], available[1] - margin)),
    )


def budget_for(
    widget: QWidget | None = None, live_area: tuple[int, int] | None = None
) -> tuple[tuple[int, int], float]:
    """The space a game grid may use here, and the scale factor to measure it at.

    Two terms, and the larger of each axis wins:

    - the **screen** term, derived from the user's configured layout. This is the floor
      of what is always achievable rather than a description of the window right now,
      because ``GameView._update_minimum_size`` raises the view's minimum when clients
      embed and Qt grows the top-level window to satisfy it. So a launch bigger than the
      current window corrects itself, provided it fits the screen — which is exactly what
      this term guarantees.
    - the **live** term, the game area as it currently is. In BartleBot, sidebar widths and
      the canvas height are startup *seeds* written only by the settings dialog — nothing
      persists a splitter drag — so the configured layout can describe something the user
      moved away from hours ago. Dragging a sidebar narrower has to unlock the tier it just made room
      for, and only a live measurement sees that.

    The live term is also better arithmetic where it applies: it has the panes, the title
    bar and the game panel's own header subtracted out
    exactly, where the screen term subtracts ``WINDOW_CHROME_ESTIMATE_PX``, an estimate.

    Taking the max rather than the live term alone is what stops a merely *small* window
    refusing a resolution the screen could carry — including at construction, before the
    window has been shown and the game view still holds a default size. Dragging a sidebar
    *wider* therefore costs nothing, which is correct: the window minimum pushes back at
    launch anyway.
    """
    screen_size, dpr = screen_metrics(widget)
    budget = game_sizing.layout_budget(screen_size, panes(), settings().pane_sizes())
    if live_area is None:
        return budget, dpr
    return (max(budget[0], live_area[0]), max(budget[1], live_area[1])), dpr


def fit_details(
    resolution: tuple[int, int],
    tiles: Tiles,
    widget: QWidget | None = None,
    live_area: tuple[int, int] | None = None,
) -> game_sizing.FitProblem | None:
    """Why *tiles* at *resolution* will not fit here, or None if they will.

    *tiles* is a count of equal tiles or each tile's size (``game_sizing.Tiles``).

    Cheap enough to call on a resize: a few settings lookups and some integer arithmetic,
    with the answer only built on the failing path. Callers on a drag re-ask rather than
    caching, so it cannot go stale.

    The structured form is what the launch screen wants: it shows the numbers on the card
    and the advice once per column, rather than the whole sentence once per card. A
    surface that shows one refusal at a time wants ``fit_problem`` instead.
    """
    budget, dpr = budget_for(widget, live_area)
    return game_sizing.fit_details(resolution, tiles, budget, dpr)


def fit_problem(
    resolution: tuple[int, int],
    tiles: Tiles,
    widget: QWidget | None = None,
    live_area: tuple[int, int] | None = None,
) -> str | None:
    """``fit_details`` as one sentence, for a tooltip or a disabled menu entry."""
    problem = fit_details(resolution, tiles, widget, live_area)
    return None if problem is None else problem.sentence


def profile_resolution(profile: dict) -> tuple[int, int]:
    """The resolution *profile* will actually launch at.

    A profile may pin its own; one that does not follows the app setting, so that is
    what its fit has to be judged against rather than a default.
    """
    pinned = profile.get("resolution")
    if pinned and len(pinned) == 2:
        return int(pinned[0]), int(pinned[1])
    return settings().game_resolution


def profile_fit_details(
    profile: dict,
    widget: QWidget | None = None,
    live_area: tuple[int, int] | None = None,
) -> game_sizing.FitProblem | None:
    """Why *profile* cannot be launched on this screen, or None if it can.

    An empty profile has nothing to fit and is refused elsewhere, on its own grounds —
    ``launch_profile`` raises for it, and the card already says so — so it is not
    reported as a sizing failure here.
    """
    accounts = profile.get("accounts") or []
    if not accounts:
        return None
    return fit_details(profile_resolution(profile), len(accounts), widget, live_area)


def profile_fit_problem(
    profile: dict,
    widget: QWidget | None = None,
    live_area: tuple[int, int] | None = None,
) -> str | None:
    """``profile_fit_details`` as one sentence, for a tooltip or a disabled menu entry."""
    problem = profile_fit_details(profile, widget, live_area)
    return None if problem is None else problem.sentence


def largest_fitting_resolution(
    tiles: Tiles,
    widget: QWidget | None = None,
    live_area: tuple[int, int] | None = None,
) -> tuple[int, int] | None:
    """The biggest offered resolution *tiles* fit at, or None if none do.

    Only ever used to *say* what would work. Nothing picks a resolution on the user's
    behalf: running four clients at 534x300 on a 4K screen to save the machine some work
    is a legitimate choice, so small resolutions are a performance lever rather than only
    a screen-space workaround, and nothing should nudge users toward the biggest that fits.
    """
    budget, dpr = budget_for(widget, live_area)
    return game_sizing.largest_fitting(VALID_RESOLUTIONS, tiles, budget, dpr)
