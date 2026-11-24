"""A dungeon sigil as a follower sees it: the leader at one, the follower's countdown, a cancel."""

import asyncio

import pytest

from bartleby.runtime.movement import sigil as sigil_module
from bartleby.runtime.movement.sigil import SEAT_MOVED, PromptReads, SigilState
from bartleby.runtime.state import Snapshot
from bartleby.runtime.testing.sim_client import SimClient
from bartleby.runtime.timing import (
    SIGIL_COUNTDOWN,
    SIGIL_COUNTDOWN_MAX,
    SIGIL_END_GRACE,
    SIGIL_PRESS_CONFIRM,
    SIGIL_SEAT_SETTLE,
    SIGIL_SEAT_WAIT,
    SIGIL_SETTLE,
)

ZONE = "WizardCity/WC_Streets/Unicorn_Way"


def snap(x, y=0.0, zone=ZONE, **kw):
    return Snapshot(key=1, time=0.0, position=(x, y, 0.0), yaw=0.0, zone=zone, **kw)


class Scene:
    """A follower's SigilState fed one tick at a time: the leader at x, the follower at 0."""

    def __init__(self) -> None:
        self.state = SigilState()
        self.now = 0.0
        self.said: list[str] = []

    def tick(
        self,
        leader_x=200.0,
        *,
        leader_prompt=None,
        mine=None,
        dt=0.1,
        me=None,
        leader=None,
        x_on_me=False,
        x_on_leader=False,
    ):
        self.now += dt
        said = self.state.note(
            self.now,
            me or snap(0),
            leader or snap(leader_x),
            leader_prompt=leader_prompt,
            my_prompt=mine,
            x_on_me=x_on_me,
            x_on_leader=x_on_leader,
        )
        if said:
            self.said.append(said)
        return said

    def stand_at_the_sigil(self) -> None:
        """The leader standing with its prompt, the follower with its own, past the settle."""
        for _ in range(int(SIGIL_SETTLE / 0.1) + 2):
            self.tick(leader_prompt=True, mine=True)

    def enter(self) -> None:
        """Interact presses X on both, and both prompts go."""
        self.stand_at_the_sigil()
        self.tick(leader_prompt=False, mine=False, x_on_me=True, x_on_leader=True)
        assert self.state.entering

    def seat_the_leader(self, at=260.0) -> None:
        """The leader walked to its seat, then stands on it."""
        self.tick(at, leader_prompt=False)
        for _ in range(int(SIGIL_SEAT_SETTLE / 0.1) + 2):
            self.tick(at, leader_prompt=False)

    def walk_the_leader_to_its_seat(self, start: float, seat: float, *, pause: float = 0.3) -> None:
        """As the game does after X: a moment still where it pressed, then 600 units a second."""
        self.tick(start, leader_prompt=False)
        for _ in range(round(pause / 0.1)):
            self.tick(start, leader_prompt=False)
        x = start
        while x < seat:
            x = min(seat, x + 60.0)
            self.tick(x, leader_prompt=False)
        for _ in range(int(SIGIL_SEAT_SETTLE / 0.1) + 2):
            self.tick(seat, leader_prompt=False)


# -- the leader at a sigil ---------------------------------------------------------------------


def test_a_leader_standing_with_its_prompt_is_at_the_sigil_once_it_has_settled():
    s = Scene()
    s.tick(leader_prompt=True)
    assert s.state.near and not s.state.at_sigil(s.now), "not before it has stood still a moment"
    s.stand_at_the_sigil()
    assert s.state.at_sigil(s.now)


def test_a_leader_walking_through_a_sigils_range_is_never_at_it():
    s = Scene()
    for i in range(20):
        s.tick(200.0 + 60 * i, leader_prompt=True)
        assert not s.state.at_sigil(s.now)


def test_a_seated_leader_is_at_the_sigil_though_its_prompt_has_gone():
    s = Scene()
    s.stand_at_the_sigil()
    s.seat_the_leader()
    assert s.state.at_sigil(s.now)


def test_a_fight_or_another_zone_forgets_the_sigil():
    s = Scene()
    s.stand_at_the_sigil()
    s.tick(leader=snap(200, in_battle=True))
    assert not s.state.near
    s.stand_at_the_sigil()
    s.tick(leader=snap(200, zone="Elsewhere"), leader_prompt=False)
    assert not s.state.near


# -- the follower's countdown -------------------------------------------------------------------


def test_x_on_it_at_a_sigil_the_leader_is_at_is_its_countdown():
    s = Scene()
    s.stand_at_the_sigil()
    assert s.tick(leader_prompt=True, mine=False, x_on_me=True) == (
        "is entering the dungeon, and holds still for its countdown"
    )
    assert s.state.entering


def test_its_prompt_going_without_a_press_is_no_countdown():
    """In game, 2026-11-20: at the edge of the range a standing follower's prompt went, and it held."""
    s = Scene()
    s.stand_at_the_sigil()
    s.tick(leader_prompt=True, mine=False)
    assert not s.state.entering


def test_x_away_from_a_sigil_the_leader_is_at_is_no_countdown():
    s = Scene()
    s.tick(mine=True)
    s.tick(mine=False, x_on_me=True)
    assert not s.state.entering


@pytest.mark.parametrize(
    "ends, said",
    [
        ({"me": snap(0, zone="Dungeon")}, "went in"),
        ({"mine": True}, "its prompt is back: its countdown was cancelled"),
    ],
    ids=["it went in", "its prompt is back"],
)
def test_a_countdown_ends_when_it_goes_in_or_its_prompt_comes_back(ends, said):
    s = Scene()
    s.enter()
    s.seat_the_leader()
    assert s.tick(260.0, leader_prompt=False, **ends) == said
    assert not s.state.entering and not s.state.cancel_pending


def test_a_countdown_no_loading_screen_follows_is_given_up():
    s = Scene()
    s.enter()
    s.tick(dt=SIGIL_COUNTDOWN_MAX + 0.1)
    assert not s.state.entering


@pytest.mark.parametrize(
    "seat",
    [500.0, 200.0 + 1438.0],
    ids=["a short walk", "from the far edge of the range"],
)
def test_a_leader_that_stood_at_its_prompt_then_walks_to_its_seat_cancels_nothing(seat):
    """In game, 2026-11-20: the wait at the prompt was counted, so the press spot was its seat."""
    s = Scene()
    for _ in range(30):  # three seconds at the prompt before the press
        s.tick(leader_prompt=True, mine=True)
    s.tick(leader_prompt=False, mine=False, x_on_me=True, x_on_leader=True)
    assert s.state.entering
    s.walk_the_leader_to_its_seat(200.0, seat)
    assert s.said == ["is entering the dungeon, and holds still for its countdown"]
    assert s.state.entering and not s.state.cancel_pending and s.state.at_sigil(s.now)
    assert s.tick(seat + SEAT_MOVED + 10, leader_prompt=False) == (
        "cancels its countdown: the leader cancelled, it left its seat"
    ), "and seated where the walk ended, so stepping off that still cancels"


def test_the_leader_is_at_the_sigil_from_its_press_through_its_walk_to_its_seat():
    """In game, 2026-11-20: between X and the seat the hold dropped, and followers walked after it."""
    s = Scene()
    s.stand_at_the_sigil()
    s.tick(leader_prompt=False, x_on_leader=True)
    x = 200.0
    while x < 800.0:
        x += 60.0
        s.tick(x, leader_prompt=False)
        assert s.state.at_sigil(s.now), f"still at the sigil at x={x}"


def test_a_leader_whose_prompt_goes_as_it_walks_off_has_walked_away_and_pressed_nothing():
    s = Scene()
    s.stand_at_the_sigil()
    for i in range(1, 8):
        s.tick(200.0 + 60 * i, leader_prompt=True)
    s.tick(200.0 + 60 * 8, leader_prompt=False)
    assert not s.state.near and not s.state.leader_pressed


def test_a_leader_s_prompt_going_just_as_its_walk_begins_is_an_x_pressed_by_hand():
    """The walk can start before a reading shows the prompt gone; the leader just stood at it."""
    s = Scene()
    s.stand_at_the_sigil()
    s.tick(260.0, leader_prompt=True)  # a reading from before the press, the walk begun
    s.tick(320.0, leader_prompt=False)
    assert s.state.leader_pressed


def test_x_on_the_follower_starts_its_countdown_though_it_walked_and_its_reading_is_stale():
    s = Scene()
    s.stand_at_the_sigil()
    assert s.tick(leader_prompt=True, mine=True, x_on_me=True) == (
        "is entering the dungeon, and holds still for its countdown"
    )
    s.tick(leader_prompt=True, mine=True)
    assert s.state.entering, "a reading from before the press is not its prompt back"
    s.tick(leader_prompt=True, mine=False)
    assert s.tick(leader_prompt=True, mine=True) == "its prompt is back: its countdown was cancelled"


def test_x_on_the_follower_whose_prompt_never_goes_did_not_take():
    s = Scene()
    s.stand_at_the_sigil()
    s.tick(leader_prompt=True, mine=True, x_on_me=True)
    pressed_at = s.now
    while s.state.entering and s.now < 5.0:
        s.tick(leader_prompt=True, mine=True)
    assert s.said[-1] == "its press did not take: its prompt never went"
    assert s.now - pressed_at <= SIGIL_PRESS_CONFIRM + 0.15


def test_x_on_a_follower_whose_reading_missed_its_prompt_is_still_its_countdown():
    """In game, 2026-11-20: P4 had just come in range, Interact pressed it, and it walked on."""
    s = Scene()
    for _ in range(int(SIGIL_SETTLE / 0.1) + 2):
        s.tick(leader_prompt=True, mine=False)
    s.tick(leader_prompt=True, mine=False, x_on_me=True)
    assert s.state.entering


def test_a_stale_reading_after_x_on_the_leader_is_not_its_prompt_back():
    s = Scene()
    s.stand_at_the_sigil()
    s.tick(leader_prompt=True, mine=False, x_on_leader=True)
    s.tick(leader_prompt=True, mine=False)
    s.tick(leader_prompt=False)
    assert s.state.leader_pressed and not s.state.cancel_pending
    s.tick(leader_prompt=True)
    assert not s.state.leader_pressed and s.state.seen[-1] == "saw the leader cancel: its prompt is back"


def test_x_on_the_leader_that_never_closes_its_prompt_did_not_take_and_its_followers_cancel():
    s = Scene()
    s.stand_at_the_sigil()
    s.tick(leader_prompt=True, mine=True, x_on_leader=True, x_on_me=True)
    assert s.state.entering
    while not s.state.cancel_pending and s.now < 5.0:
        s.tick(leader_prompt=True, mine=False)
    assert s.said[-1] == "cancels its countdown: the leader cancelled, its press did not take"


def test_what_it_saw_of_the_leader_is_kept_for_the_log():
    s = Scene()
    s.stand_at_the_sigil()
    s.tick(leader_prompt=False, x_on_leader=True)
    s.seat_the_leader()
    s.tick(260.0, leader_prompt=True)
    assert s.state.seen[0] == "X was pressed on the leader at the sigil"
    assert s.state.seen[1].startswith("saw the leader seated, after ")
    assert s.state.seen[2] == "saw the leader cancel: its prompt is back"


# -- the countdown's end -----------------------------------------------------------------------


def _to_the_end(s: Scene, at=260.0) -> None:
    """Enter, seat the leader, and wait out the game's countdown."""
    s.enter()
    pressed_at = s.now
    s.seat_the_leader(at)
    while s.now - pressed_at < SIGIL_COUNTDOWN + 0.3:
        s.tick(at, leader_prompt=False, mine=False)


def test_the_leader_s_prompt_back_as_its_countdown_ends_is_no_cancel_and_it_goes_in():
    """In game, 2026-11-20: the prompt came back 11.1 s after the press, and the leader went in."""
    s = Scene()
    _to_the_end(s)
    s.tick(260.0, leader_prompt=True, mine=True)
    s.tick(260.0, leader_prompt=False, mine=False)
    assert not s.state.cancel_pending and s.state.entering
    assert s.state.seen[-1].startswith("saw the leader's countdown end (its prompt is back), after ")
    s.tick(leader=snap(260, loading=True), me=snap(0, loading=True))
    assert s.tick(leader=snap(0, zone="Dungeon"), me=snap(0, zone="Dungeon")) == "went in"
    assert not s.state.cancel_pending
    assert s.state.went_in, "the follower takes it, so its zone change is no move to log"


def test_the_leader_moving_as_its_countdown_ends_is_no_cancel():
    """Its position can move to the dungeon's before its zone does."""
    s = Scene()
    _to_the_end(s)
    s.tick(5000.0, leader_prompt=False, mine=False)
    assert not s.state.cancel_pending and s.state.entering


def test_a_leader_still_outside_well_after_its_countdown_cancelled_late():
    s = Scene()
    _to_the_end(s)
    s.tick(260.0, leader_prompt=True, mine=False)
    while not s.state.cancel_pending and s.now < SIGIL_COUNTDOWN + SIGIL_END_GRACE + 2.0:
        s.tick(260.0, leader_prompt=True, mine=False)
    assert s.said[-1] == "cancels its countdown: the leader cancelled, it stayed out after its countdown"


def test_its_own_prompt_back_as_its_countdown_ends_keeps_it_still_until_it_goes_in():
    s = Scene()
    _to_the_end(s)
    s.tick(260.0, leader_prompt=False, mine=True)
    assert s.state.entering
    assert s.tick(me=snap(0, zone="Dungeon"), leader=snap(0, zone="Dungeon")) == "went in"


def test_its_own_prompt_back_after_its_countdown_with_no_loading_follows_again_in_time():
    s = Scene()
    _to_the_end(s)
    s.tick(260.0, leader_prompt=False, mine=True)
    while s.state.entering and s.now < SIGIL_COUNTDOWN + SIGIL_END_GRACE + 2.0:
        s.tick(260.0, leader_prompt=False, mine=True)
    assert s.said[-1] == "stayed out after its countdown"


def test_the_leader_walking_to_its_seat_and_going_in_cancels_nothing():
    s = Scene()
    s.enter()
    s.seat_the_leader()
    s.tick(leader=snap(260, loading=True))  # unreadable: the leader's loading screen
    s.tick(leader=snap(0, zone="Dungeon"))
    assert s.state.entering and not s.state.cancel_pending


# -- the leader cancels --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "move, why",
    [
        ({"leader_x": 260.0, "leader_prompt": True}, "its prompt is back"),
        ({"leader_x": 260.0 + SEAT_MOVED + 10}, "it left its seat"),
    ],
    ids=["it turned", "it stepped off"],
)
def test_a_seated_leader_that_cancels_has_an_entering_follower_cancel_too(move, why):
    s = Scene()
    s.enter()
    s.seat_the_leader()
    assert s.tick(**{"leader_prompt": False, **move}) == f"cancels its countdown: the leader cancelled, {why}"
    assert s.state.cancel_pending and s.state.entering
    s.state.cancelled()
    assert not s.state.entering and not s.state.cancel_pending


def test_a_leader_never_seated_walked_away_and_an_entering_follower_cancels():
    s = Scene()
    s.enter()
    x = 200.0
    while not s.state.cancel_pending and s.now < 2 * SIGIL_SEAT_WAIT:
        x += 60.0
        s.tick(x, leader_prompt=False)
    assert s.said[-1] == "cancels its countdown: the leader cancelled, it walked away"


def test_a_leader_cancelling_asks_nothing_of_a_follower_not_in_a_countdown():
    s = Scene()
    s.stand_at_the_sigil()
    s.seat_the_leader()
    s.tick(260.0, leader_prompt=True, mine=True)
    assert not s.state.cancel_pending and not s.state.entering


def test_a_leader_stood_still_long_past_any_countdown_is_no_longer_at_the_sigil():
    s = Scene()
    s.stand_at_the_sigil()
    s.seat_the_leader()
    s.tick(260.0, leader_prompt=False, dt=SIGIL_COUNTDOWN_MAX + 0.1)
    assert not s.state.near


# -- PromptReads ----------------------------------------------------------------------------------


class _Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


async def test_a_prompt_is_read_once_per_interval_and_shared():
    reads = []
    showing = {"value": True}

    async def read(client):
        reads.append(client)
        await asyncio.sleep(0.01)
        return showing["value"]

    clock, client = _Clock(), SimClient()
    prompts = PromptReads(read, interval=0.2, clock=clock)
    first = await asyncio.gather(*(prompts.showing(client) for _ in range(3)))
    assert first == [True, True, True] and len(reads) == 1, "three at once share one reading"
    showing["value"] = False
    clock.now = 0.1
    assert await prompts.showing(client) is True and len(reads) == 1
    clock.now = 0.3
    assert await prompts.showing(client) is False and len(reads) == 2


async def test_a_reading_that_fails_cannot_tell():
    async def read(_client):
        raise RuntimeError("the UI tree moved")

    assert await PromptReads(read).showing(SimClient()) is None


async def test_the_prompt_read_is_the_team_up_button_on_the_entrance_prompt(monkeypatch):
    seen = []

    async def visible(client, path):
        seen.append(path)
        return True

    monkeypatch.setattr(sigil_module.node_helpers, "is_visible_by_path", visible)
    assert await sigil_module.at_dungeon_sigil(SimClient())
    assert seen == [["WorldView", "NPCRangeWin", "imgBackground", "TeamUpButton"]]
