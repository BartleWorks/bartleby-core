"""A dungeon sigil as a follower sees it: the leader at one, its own countdown, and a cancel.

How the game enters a dungeon (``docs/WIZARD101.md``, *Dungeon sigils and relogging*): a
wizard in range of a sigil sees its entrance prompt, which carries a Team Up button on every
dungeon sigil and on no other prompt. Pressing X closes the prompt, walks the wizard to a seat on
the sigil, and starts a 10 s countdown, which everyone who presses X within it shares. Any
movement, a turn included, cancels a wizard's countdown, and its prompt comes back within about
a second; one wizard cancelling does not cancel anyone else's.

So a follower must not move from the moment it presses X, and a leader that cancels must take
its followers out with it. :class:`SigilState` follows both from presses, prompt readings and
positions, and :class:`~bartleby.runtime.movement.follow.Follower` acts on what it says:

* **At the sigil.** The leader stands still with its prompt showing, or has pressed X, from its
  press through the game's walk to its seat and its countdown. A follower with its own prompt
  stands where it is, so it stays in range for the press; one without walks straight at the
  leader, which is in range, until its prompt shows.
* **Entering.** X is pressed on the follower while the leader is at the sigil, which the
  arbiter reports as an Interact lease on its client. Nothing else counts: a follower's window
  is the player's while focused, so no X is pressed on it by hand, and its prompt flickers at
  the edge of the range. From then it sends nothing at all, whatever the leader does, until it
  loads or changes zone, its prompt comes back, or :data:`SIGIL_COUNTDOWN_MAX` passes.
* **The leader cancels.** Its prompt coming back means it cancelled, and so, once it is seated
  (still for :data:`~bartleby.runtime.timing.SIGIL_SEAT_SETTLE` since its press), does its
  moving more than :data:`SEAT_MOVED` from its seat; a leader that is not seated within
  :data:`~bartleby.runtime.timing.SIGIL_SEAT_WAIT` of its press walked away. Either way a
  follower entering cancels too, with a short turn, so the team stays on one side of the door.
* **The countdown ends.** The game shows the prompt again for a moment before the loading
  screen. So from :data:`~bartleby.runtime.timing.SIGIL_COUNTDOWN` after a press, a prompt
  back, or a leader off its seat, cancels nothing until
  :data:`~bartleby.runtime.timing.SIGIL_END_GRACE` has passed with no zone change.

A prompt reading can be up to :data:`~bartleby.runtime.timing.SIGIL_READ_INTERVAL` old, so one
taken just before a press can still show the prompt after it: a prompt counts as back only once
it has been seen gone, and one never seen gone within
:data:`~bartleby.runtime.timing.SIGIL_PRESS_CONFIRM` means the press did not take.

Reading a prompt walks the UI tree, so :class:`PromptReads` takes at most one reading of a
client per :data:`~bartleby.runtime.timing.SIGIL_READ_INTERVAL` and shares it among a team's
followers. Gauntlets have no Team Up button, and are not covered.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable

from bartleby.runtime.actions import node_helpers
from bartleby.runtime.movement.geometry import Point, flat_distance
from bartleby.runtime.paths import DUNGEON_ENTRANCE_TEAMUP_BUTTON_PATH
from bartleby.runtime.state import Snapshot
from bartleby.runtime.timing import (
    SIGIL_COUNTDOWN,
    SIGIL_COUNTDOWN_MAX,
    SIGIL_END_GRACE,
    SIGIL_PRESS_CONFIRM,
    SIGIL_PRESS_LAG,
    SIGIL_READ_INTERVAL,
    SIGIL_SEAT_SETTLE,
    SIGIL_SEAT_WAIT,
    SIGIL_SETTLE,
)

#: Units the leader may drift and still count as standing at the sigil.
SIGIL_STILL = 30.0
#: Units a seated leader moves from its seat that mean it cancelled its countdown.
SEAT_MOVED = 60.0


async def at_dungeon_sigil(client) -> bool:
    """Whether *client*'s dungeon entrance prompt shows: the interact prompt with Team Up on it.

    The prompt window exists only while the wizard is in range, so the path resolves only then.
    """
    return await node_helpers.is_visible_by_path(client, DUNGEON_ENTRANCE_TEAMUP_BUTTON_PATH)


class PromptReads:
    """Each client's dungeon entrance prompt, read at most once per :data:`SIGIL_READ_INTERVAL`.

    Shared by a team's followers, so the leader's prompt is read once for all of them, and two
    asking at once share one reading. A reading that fails is None: cannot tell.
    """

    def __init__(
        self,
        read: Callable[[object], Awaitable[bool]] = at_dungeon_sigil,
        *,
        interval: float = SIGIL_READ_INTERVAL,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._read = read
        self._interval = interval
        self._clock = clock
        self._last: dict[int, tuple[float, bool | None]] = {}
        self._pending: dict[int, asyncio.Task] = {}

    async def showing(self, client) -> bool | None:
        key = client.window_handle
        last = self._last.get(key)
        if last is not None and self._clock() - last[0] < self._interval:
            return last[1]
        task = self._pending.get(key)
        if task is None:
            task = asyncio.ensure_future(self._take(client))
            self._pending[key] = task
            task.add_done_callback(lambda _t, key=key: self._pending.pop(key, None))
        return await asyncio.shield(task)

    async def _take(self, client) -> bool | None:
        try:
            value: bool | None = bool(await self._read(client))
        except Exception:
            value = None
        self._last[client.window_handle] = (self._clock(), value)
        return value


class SigilState:
    """One follower's view of a dungeon sigil, from presses, prompt readings and positions.

    :meth:`note` takes each tick's readings; the properties say what follows from them, and
    :attr:`seen` keeps what it saw of the leader, for the log.
    """

    def __init__(self) -> None:
        # The leader's side.
        self._leader_zone: str | None = None
        self._leader_prompt = False
        self._settled_at: float | None = None
        self._pressed_at: float | None = None
        self._leader_seen_gone = False
        self._leader_ending_since: float | None = None
        self._seat: Point | None = None
        self._leader_still_at: tuple[Point, float] | None = None
        # The follower's side.
        self._my_prompt: bool | None = None
        self._entering_since: float | None = None
        self._entering_zone: str | None = None
        self._my_seen_gone = False
        self._my_ending_since: float | None = None
        self.cancel_pending = False
        #: What it saw of the leader at a sigil, oldest first, for the log to take.
        self.seen: list[str] = []

    # -- what it says ----------------------------------------------------------------------

    @property
    def my_prompt(self) -> bool | None:
        """The follower's own prompt at its last reading, or None for none yet."""
        return self._my_prompt

    @property
    def entering(self) -> bool:
        """Whether the follower is in its countdown, so must send nothing."""
        return self._entering_since is not None

    @property
    def leader_pressed(self) -> bool:
        """Whether the leader has pressed X at a sigil and not yet cancelled or gone in."""
        return self._pressed_at is not None

    @property
    def near(self) -> bool:
        """Whether the leader is at a sigil: its prompt shows, or it has pressed X."""
        return self._leader_prompt or self.leader_pressed

    def at_sigil(self, now: float) -> bool:
        """Whether the leader stands at a sigil: still with its prompt showing, or pressed."""
        if self.leader_pressed:
            return True
        return self._leader_prompt and self._leader_still(now) >= SIGIL_SETTLE

    def wants_my_prompt(self) -> bool:
        """Whether the follower's own prompt is worth reading this tick."""
        return self.near or self.entering

    # -- taking readings ---------------------------------------------------------------------

    def note(
        self,
        now: float,
        me: Snapshot,
        leader: Snapshot,
        *,
        leader_prompt: bool | None,
        my_prompt: bool | None,
        x_on_me: bool = False,
        x_on_leader: bool = False,
    ) -> str | None:
        """Take this tick's readings.

        *x_on_me* and *x_on_leader*: an Interact was pressed on the follower or on the leader
        since the last tick. Returns what happened to the follower worth logging, or None.
        """
        said = None
        if leader.readable:
            said = self._note_leader(now, leader, leader_prompt, x_on_leader)
        if me.readable:
            said = self._note_me(now, me, my_prompt, x_on_me) or said
        return said

    def _note_leader(self, now: float, leader: Snapshot, prompt: bool | None, x: bool) -> str | None:
        if leader.zone != self._leader_zone or leader.in_battle:
            self._leader_zone = leader.zone
            self._forget_leader()
            if leader.in_battle:
                return None
        if self._leader_prompt and self._leader_still(now) >= SIGIL_SETTLE:
            self._settled_at = now
        if self._leader_still_at is None or flat_distance(leader.position, self._leader_still_at[0]) > SIGIL_STILL:
            self._leader_still_at = (leader.position, now)
        if x and not self.leader_pressed and (self._leader_prompt or prompt is True):
            self._press(now, leader, "X was pressed on the leader at the sigil")
        if self.leader_pressed:
            return self._note_pressed_leader(now, leader, prompt)
        if prompt is True:
            self._leader_prompt = True
        elif prompt is False and self._leader_prompt:
            self._leader_prompt = False
            if self._settled_at is not None and now - self._settled_at <= SIGIL_PRESS_LAG:
                # Gone while it stood at the sigil, or had just begun the walk to its seat: an X
                # pressed by hand on the leader's own window.
                self._press(now, leader, "the leader's prompt went as it stood at the sigil")
                self._leader_seen_gone = True
        return None

    def _note_pressed_leader(self, now: float, leader: Snapshot, prompt: bool | None) -> str | None:
        pressed_for = now - self._pressed_at
        if self._leader_ending_since is not None:
            # Its countdown is over: it goes in, which a zone change shows, or it stayed out.
            if now - self._leader_ending_since > SIGIL_END_GRACE:
                return self._leader_cancelled("it stayed out after its countdown", prompt_back=prompt is True)
            return None
        if prompt is False:
            self._leader_seen_gone = True
        elif prompt is True:
            if self._leader_seen_gone:
                return self._ending_or_cancelled(now, pressed_for, "its prompt is back", prompt_back=True)
            if pressed_for > SIGIL_PRESS_CONFIRM:
                return self._leader_cancelled("its press did not take", prompt_back=True)
            # A reading from before the press: wait for one after it.
        if self._seat is None:
            if self._leader_still(now) >= SIGIL_SEAT_SETTLE:
                self._seat = leader.position
                self.seen.append(f"saw the leader seated, after {pressed_for:.1f} s")
            elif pressed_for > SIGIL_SEAT_WAIT:
                return self._leader_cancelled("it walked away")
        elif flat_distance(leader.position, self._seat) > SEAT_MOVED:
            return self._ending_or_cancelled(now, pressed_for, "it left its seat")
        if pressed_for > SIGIL_COUNTDOWN_MAX:
            # Long past any countdown: a leader that stayed outside, not one entering.
            self.seen.append("forgot the leader's press: no countdown is that long")
            self._forget_leader()
        return None

    def _press(self, now: float, leader: Snapshot, what: str) -> None:
        self._pressed_at = now
        self._leader_prompt = False
        self._leader_seen_gone = False
        self._seat = None
        # Its stillness counts from the press: it stood at the prompt before it, and the game's
        # walk to its seat starts a moment after.
        self._leader_still_at = (leader.position, now)
        self.seen.append(what)

    def _ending_or_cancelled(
        self, now: float, pressed_for: float, why: str, *, prompt_back: bool = False
    ) -> str | None:
        """A sign of a cancel: a cancel before the countdown can have ended, else perhaps its end.

        As a countdown ends the game shows the prompt again for a moment before the loading
        screen, and the position may move before the zone does: so from :data:`SIGIL_COUNTDOWN`
        after the press, the leader only cancelled if it is still outside a while later.
        """
        if pressed_for < SIGIL_COUNTDOWN:
            return self._leader_cancelled(why, prompt_back=prompt_back)
        self._leader_ending_since = now
        self.seen.append(f"saw the leader's countdown end ({why}), after {pressed_for:.1f} s")
        return None

    def _note_me(self, now: float, me: Snapshot, prompt: bool | None, x: bool) -> str | None:
        self._my_prompt = prompt if prompt is not None else self._my_prompt
        if self.entering:
            return self._note_my_countdown(now, me, prompt)
        if x and self.near:
            # Interact presses only a wizard in range of something, and the leader is at a
            # sigil: a reading that missed the prompt is older than the press, or its edge.
            self._start_entering(now, me, seen_gone=prompt is False)
            return "is entering the dungeon, and holds still for its countdown"
        return None

    def _note_my_countdown(self, now: float, me: Snapshot, prompt: bool | None) -> str | None:
        since = now - self._entering_since
        if me.zone != self._entering_zone:
            self._stop_entering()
            return "went in"
        if self._my_ending_since is not None:
            if now - self._my_ending_since > SIGIL_END_GRACE:
                self._stop_entering()
                return "stayed out after its countdown"
        elif prompt is False:
            self._my_seen_gone = True
        elif prompt is True:
            if self._my_seen_gone and since >= SIGIL_COUNTDOWN:
                # The game shows it again as a countdown ends, a moment before the loading screen.
                self._my_ending_since = now
            elif self._my_seen_gone:
                self._stop_entering()
                return "its prompt is back: its countdown was cancelled"
            elif since > SIGIL_PRESS_CONFIRM:
                self._stop_entering()
                return "its press did not take: its prompt never went"
        if since > SIGIL_COUNTDOWN_MAX:
            self._stop_entering()
            return "no loading screen came"
        return None

    def _start_entering(self, now: float, me: Snapshot, *, seen_gone: bool) -> None:
        self._entering_since = now
        self._entering_zone = me.zone
        self._my_seen_gone = seen_gone

    def _leader_cancelled(self, why: str, *, prompt_back: bool = False) -> str | None:
        self.seen.append(f"saw the leader cancel: {why}")
        self._forget_leader()
        self._leader_prompt = prompt_back
        if not self.entering:
            return None
        self.cancel_pending = True
        return f"cancels its countdown: the leader cancelled, {why}"

    def cancelled(self) -> None:
        """The follower has cancelled its countdown: it follows again."""
        self._stop_entering()

    def _stop_entering(self) -> None:
        self._entering_since = None
        self._entering_zone = None
        self._my_seen_gone = False
        self._my_ending_since = None
        self.cancel_pending = False

    def _forget_leader(self) -> None:
        self._leader_prompt = False
        self._settled_at = None
        self._pressed_at = None
        self._leader_seen_gone = False
        self._leader_ending_since = None
        self._seat = None

    def _leader_still(self, now: float) -> float:
        return 0.0 if self._leader_still_at is None else now - self._leader_still_at[1]
