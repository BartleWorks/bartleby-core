"""A launching client's tile: its caption, its spinner, and a failed launch's way out.

The tiles the client manager asks for while an account launches are
``bartleby-core``'s ``tests/runtime/clients/test_launch_pending.py``; this is how a
``ClientTile`` draws one.
"""

from bartleby.qt.game_embed.client_tile import launch_status_text
from bartleby.runtime.clients.tile_slots import build_tile_slots
from bartleby.runtime.timing import LAUNCH_STAGE_ELAPSED_HINT


class TestLaunchStatusText:
    def test_a_tile_with_no_report_yet_still_says_something(self) -> None:
        """A bare collection of usernames carries no stages, and the tile must survive it."""
        (tile,) = build_tile_slots(
            client_order=["alpha"],
            hwnd_accounts={},
            embeddable_hwnds=set(),
            client_hwnds=[],
            account_label=str.title,
            launching=["alpha"],
        )
        assert tile.status is None
        assert launch_status_text(tile.status, tile.status_since) == "Launching…"

    def test_a_young_stage_shows_no_number(self) -> None:
        text = launch_status_text("Loading", 100.0, now=100.0 + LAUNCH_STAGE_ELAPSED_HINT - 1)
        assert text == "Loading…"

    def test_a_stage_that_has_run_long_shows_how_long(self) -> None:
        """The one thing that separates a stalled launch from a healthy one on screen."""
        assert launch_status_text("Logging in", 100.0, now=145.0) == "Logging in… 45s"

    def test_the_number_appears_once_the_stage_has_run_the_threshold(self) -> None:
        text = launch_status_text("Loading", 0.0, now=LAUNCH_STAGE_ELAPSED_HINT)
        assert text == f"Loading… {int(LAUNCH_STAGE_ELAPSED_HINT)}s"

    def test_a_stage_with_no_clock_never_shows_a_number(self) -> None:
        assert launch_status_text("Launching game", None, now=9999.0) == "Launching game…"


class TestSpinnerMotion:
    """The spinner has to stay smooth through the launch's own event-loop stalls."""

    def test_the_angle_comes_from_the_clock_not_from_a_tick_count(self, qapp) -> None:
        """A tick the event loop swallows costs a frame, not a step. Counting steps instead
        turns every synchronous memory read during a launch into a visible hitch."""
        import time

        from bartleby.qt.game_embed.client_tile import _SPINNER_PERIOD_MS, PendingTile

        tile = PendingTile("Alpha")
        tile._spin_start = time.monotonic() - _SPINNER_PERIOD_MS / 1000 / 4
        first = tile._angle
        assert 80 < first < 100, f"a quarter turn should be near 90 degrees, got {first}"

        # No ticks at all in between: the angle still advances, because nothing about it
        # is accumulated.
        tile._spin_start -= _SPINNER_PERIOD_MS / 1000 / 4
        assert 170 < tile._angle < 190
        tile.deleteLater()

    def test_a_tick_repaints_the_ring_and_leaves_the_rest_alone(self, qapp) -> None:
        """At 60fps, repainting the whole tile would redraw an unchanged caption and pill
        sixty times a second, for every pending tile at once."""
        from bartleby.qt.game_embed.client_tile import PendingTile

        tile = PendingTile("Alpha")
        tile.resize(400, 300)
        asked: list[object] = []
        tile.update = lambda *args: asked.append(args)  # type: ignore[method-assign]

        tile._painted_text = tile.status_text
        tile._advance()
        assert asked == [(tile._spinner_rect(),)]
        assert not tile._spinner_rect().isEmpty()
        tile.deleteLater()

    def test_a_changed_caption_repaints_the_whole_tile(self, qapp) -> None:
        """The caption ages on the clock rather than on a report, so the tick is also what
        notices it has changed — a partial repaint alone would freeze the elapsed hint."""
        from bartleby.qt.game_embed.client_tile import PendingTile

        tile = PendingTile("Alpha")
        tile.resize(400, 300)
        asked: list[object] = []
        tile.update = lambda *args: asked.append(args)  # type: ignore[method-assign]

        tile._painted_text = "something else entirely"
        tile._advance()
        assert asked == [()], "a caption that changed did not get a full repaint"
        tile.deleteLater()


class TestFailedTileRendering:
    def test_a_failed_caption_drops_the_ellipsis_and_the_clock(self) -> None:
        """It is not still doing the thing, and how long it has been finished is not news."""
        assert launch_status_text("Launch failed", 0.0, now=9999.0, failed=True) == "Launch failed"

    def test_a_failed_tile_stops_spinning_and_offers_a_way_out(self, qapp) -> None:
        from bartleby.qt.game_embed.client_tile import PendingTile

        tile = PendingTile("Alpha")
        assert tile._spin_timer.isActive()

        tile.set_status("Launch failed", None, failed=True)
        assert tile.failed
        assert not tile._spin_timer.isActive(), "a stopped launch kept repainting itself"
        assert tile.status_text == "Launch failed"
        tile.deleteLater()
