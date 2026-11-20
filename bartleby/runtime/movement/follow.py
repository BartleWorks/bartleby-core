"""Following the leader: walk its trail with the follower's own keys, porting only as a fallback.

Each follower reads the leader's snapshots (:mod:`bartleby.runtime.state`), which build the
leader's :class:`~bartleby.runtime.movement.trail.Trail`, and walks that trail of points the
way BartleBot's walking probe follows a route: forward held, facing written toward the next
point every :data:`~bartleby.runtime.timing.FOLLOW_TICK`, the server's resets absorbed by
reading the position back every tick, and a stall backed off from.

* **No route planning.** The trail is ground the leader just crossed.
* **Speed closes the gap.** A follower further back than :data:`CATCH_UP` runs at 2.0x, the
  most the engine allows; nearer, it walks at ``walking_speed``, or the leader's own speed
  field without one. A follower that shares its speed field with another writer, as with
  BartleBuddy's Speed toggle, shares a :class:`~bartleby.runtime.actions.speed_ledger.SpeedLedger`
  with it, so neither gives back the other's value as the wizard's own.
* **Shortcuts follow the trail.** At a jump it presses Interact; a trail ending at a gate
  takes it through after the leader.
* **Ports are the fallback**, for a follower too far behind, a jump or gate that did not
  take it, a stall it cannot back out of, or a leader somewhere the trail does not reach.
* **Spacing.** Each follower stops at its own gap behind a moving leader, and takes its own
  place around a leader standing still (:func:`spacing_for`).
* **Fights.** A fight the leader is in is joined by walking into it; once the follower is in
  one itself it does nothing, since a fight is combat's.
* **Waiting for the leader to move**, when asked to (``wait_for_move``): nothing until the
  leader has passed :data:`STILL`, changed zone or entered a fight. A product that picks a
  new leader for the player, as BartleBuddy's swap does, uses it so that looking at a
  wizard doesn't send the team running, nor a follower that logged in elsewhere straight
  into a port.
* **Held**, while ``hold`` says so: it stands where it is, unless the leader is in a fight,
  which it still joins, and stays where the fight leaves it. Let go, it stops waiting for the
  leader to move, since the player just asked for the team back.
* **Far means port**, when asked to (``port_when_far``): a follower beyond :data:`RESYNC` of
  its leader when it starts, once any wait is over, or when it's let go, ports rather than
  walking at the leader through whatever is between them.

:meth:`Follower.plan` decides the next step from the snapshots alone, and is where the
rules above live; :meth:`Follower.run` carries steps out under a movement lease, holding it
only while it moves, so a hotkey on a follower standing still takes nothing from it.

Not here yet: steering around a mob that has moved onto the trail since the leader crossed
it. The probe's mob watcher does that, and comes over with route planning.
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum

from bartleby.runtime.actions.live_character import CharacterWatch, live_character_id
from bartleby.runtime.actions.speed_ledger import SpeedLedger
from bartleby.runtime.arbiter import Arbiter, Lease, Owner, Preempted, Revoked
from bartleby.runtime.game.key_bindings import GameAction
from bartleby.runtime.game.wizard_names import NO_CHARACTER
from bartleby.runtime.input import SPEED_VALUE_MAX
from bartleby.runtime.movement.geometry import Point, flat_distance, heading_to, offset_from
from bartleby.runtime.movement.trail import Mark, Trail, TrailPoint
from bartleby.runtime.state import Snapshot, StateReader
from bartleby.runtime.timing import (
    FOLLOW_BACK_OFF,
    FOLLOW_GATE_WAIT,
    FOLLOW_LINK_WAIT,
    FOLLOW_PORT_RETRY,
    FOLLOW_PROMPT_WAIT,
    FOLLOW_STALL_WINDOW,
    FOLLOW_TICK,
    LEADER_SETTLE,
)

logger = logging.getLogger(__name__)

#: Units from a point at which it counts as reached; the walking probe's.
ARRIVE = 80.0
#: Units from its place around a still leader at which a follower stops.
ARRIVE_PLACE = 40.0
#: Units the leader may drift and still count as standing still.
STILL = 30.0
#: Units behind beyond which a follower runs at 2.0x.
CATCH_UP = 500.0
#: Units of trail ahead a follower looks for the furthest point it has already reached.
LOOK_AHEAD = 12
#: Units from the next point beyond which a follower has lost the trail and finds it again.
RESYNC = 1500.0
#: Units of progress under which forward held for the stall window is a stall.
STALL_MIN_MOVE = 60.0
#: Stalls on the way to one point before a follower gives up walking and ports.
MAX_STALLS = 3
#: Units a follower must be moved at a jump for the jump to have taken it.
LINK_MOVED = 300.0
#: Units past a gate's last point a follower aims for, to walk on through the door.
GATE_PUSH = 250.0
#: Units from a still leader within which a follower walks straight to its place.
PLACE_DIRECT = 600.0

#: Why a follower made with ``wait_for_move`` stands, until its leader has moved.
AWAITING_MOVE = "waiting for the leader to move"
#: Why a held follower stands.
HELD = "held where it is"


class StepKind(StrEnum):
    WAIT = "wait"  # nothing to do for now: loading, in a fight, or nothing readable
    STOP = "stop"  # in place
    WALK = "walk"
    INTERACT = "interact"
    PORT = "port"


@dataclass(frozen=True)
class Step:
    kind: StepKind
    target: Point | None = None
    fast: bool = False
    reason: str = ""
    point: TrailPoint | None = None


@dataclass(frozen=True)
class Spacing:
    """Where one follower keeps itself: *gap* behind a moving leader, at *angle* and *radius*
    around a still one (an angle of pi is straight behind)."""

    gap: float
    angle: float
    radius: float


def spacing_for(index: int) -> Spacing:
    """The spacing of the *index*-th follower of one leader: behind-left, behind-right, behind."""
    angles = (math.pi - 0.7, math.pi + 0.7, math.pi)
    return Spacing(gap=180.0 + 80.0 * index, angle=angles[index % 3], radius=170.0 + 90.0 * (index // 3))


#: Ports the follower to its leader through the friends list, under the lease it is given.
#: True when it landed with the leader.
PortToLeader = Callable[[Lease], Awaitable[bool]]


class Follower:
    """One client following one leader along its trail."""

    def __init__(
        self,
        client,
        me: StateReader,
        leader: StateReader,
        trail: Trail,
        arbiter: Arbiter,
        *,
        spacing: Spacing,
        port: PortToLeader | None = None,
        label: str = "",
        clock: Callable[[], float] = time.monotonic,
        wait_for_move: bool = False,
        hold: Callable[[], bool] = lambda: False,
        port_when_far: bool = False,
        walking_speed: Callable[[], int | None] | None = None,
        speed_ledger: SpeedLedger | None = None,
    ) -> None:
        """With *wait_for_move*, it stands until the leader has moved from where it first reads it.

        While *hold* answers True it stands, joining only the leader's fights. With
        *port_when_far*, it ports when it starts, or is let go, beyond :data:`RESYNC` of the
        leader. *walking_speed* is the speed field's value to walk at when not catching up,
        or None for the leader's own; a follower given one stands at its own speed when it
        says None, since nothing else then holds the field. *speed_ledger* is shared with any other writer of the
        field (:mod:`bartleby.runtime.actions.speed_ledger`).
        """
        self.client = client
        self.me = me
        self.leader = leader
        self.trail = trail
        self.arbiter = arbiter
        self.spacing = spacing
        self.port = port
        self.label = label or f"window {client.window_handle}"
        self._clock = clock
        self.cursor: int | None = None
        self.position: Point | None = None
        self._leader_still_at: tuple[Point, float] | None = None
        self._gate_since: float | None = None
        self._jump_failed: int | None = None
        self._port_after = 0.0
        self._stalls = 0
        self._stall_from: tuple[Point, float] | None = None
        self._speed_watch = CharacterWatch()
        self._ledger = speed_ledger if speed_ledger is not None else SpeedLedger()
        self._walking_speed = walking_speed
        self.last_step = Step(StepKind.WAIT)
        self._awaiting_move = wait_for_move
        self._leader_from: Snapshot | None = None
        self._hold = hold
        self._held = False
        self._port_when_far = port_when_far
        self._far_check = port_when_far
        self._walking = False

    # -- deciding -----------------------------------------------------------------------

    def plan(self, me: Snapshot, position: Point | None, leader: Snapshot, now: float) -> Step:
        """The next step, from the follower's snapshot and position and the leader's."""
        if position is None or not me.readable or not leader.readable:
            return Step(StepKind.WAIT, reason="not readable")
        if me.in_battle:
            return Step(StepKind.WAIT, reason="in a fight")
        held = self._hold()
        if held:
            self._held = True
        elif self._held:
            self._let_go()
        if self._awaiting_move and not self._leader_has_moved(leader):
            return Step(StepKind.WAIT, reason=AWAITING_MOVE)
        if held and not leader.in_battle:
            return Step(StepKind.STOP, reason=HELD)
        if now < self._port_after:
            return Step(StepKind.WAIT, reason="waiting to port again")
        self._note_leader(leader, now)
        if self.cursor is None:
            nearest = self.trail.nearest(position, me.zone, self.trail.first_seq)
            self.cursor = nearest.seq if nearest else self.trail.end_seq
        if self.cursor < self.trail.first_seq:
            return Step(StepKind.PORT, reason="fell too far behind the trail")

        if leader.zone != me.zone:
            gate = next((p for p in self.trail.after(self.cursor) if p.zone == me.zone and p.mark is Mark.GATE), None)
            if gate is None:
                return Step(StepKind.PORT, reason="the leader is in another zone the trail does not reach")
            if self._gate_since is None:
                self._gate_since = now
            if now - self._gate_since > FOLLOW_GATE_WAIT:
                return Step(StepKind.PORT, reason="the gate did not take it")
            return Step(StepKind.WALK, self._beyond(gate), fast=True, reason="through the gate", point=gate)
        self._gate_since = None

        distance = flat_distance(position, leader.position)
        if self._far_check:
            if distance > RESYNC:
                return Step(StepKind.PORT, reason="far from the leader")
            self._far_check = False
        if leader.in_battle:
            return Step(StepKind.WALK, leader.position, fast=True, reason="joining the fight")

        point = self._advance(position, me.zone)
        if point is not None and point.mark is Mark.JUMP and flat_distance(position, point.position) <= ARRIVE:
            if self._jump_failed == point.seq:
                return Step(StepKind.PORT, reason="the jump did not take it")
            return Step(StepKind.INTERACT, point.position, reason="at a jump", point=point)
        if self._stalls >= MAX_STALLS:
            return Step(StepKind.PORT, reason="stalled")

        fast = distance > CATCH_UP
        if self._leader_still(now) and (point is None or distance <= PLACE_DIRECT):
            place = offset_from(leader.position, leader.yaw or 0.0, self.spacing.angle, self.spacing.radius)
            if flat_distance(position, place) <= ARRIVE_PLACE:
                return Step(StepKind.STOP, reason="in place")
            return Step(StepKind.WALK, place, fast=fast, reason="to its place")
        if distance <= self.spacing.gap:
            return Step(StepKind.STOP, reason="close enough")
        if point is None:
            return Step(StepKind.WALK, leader.position, fast=fast, reason="toward the leader")
        return Step(StepKind.WALK, point.position, fast=fast, reason="along the trail", point=point)

    def _advance(self, position: Point, zone: str) -> TrailPoint | None:
        """Move the cursor past every point already reached, and return the next one in *zone*."""
        ahead = [p for p in self.trail.after(self.cursor) if p.zone == zone][:LOOK_AHEAD]
        for point in reversed(ahead):
            if flat_distance(point.position, position) <= ARRIVE:
                if point.mark is not Mark.WALK:
                    # Reached a jump or a gate: stay on it until it has taken the follower.
                    self._reached(point.seq)
                    return point
                self._reached(point.seq + 1)
                break
        next_point = next((p for p in self.trail.after(self.cursor) if p.zone == zone), None)
        if next_point is not None and flat_distance(next_point.position, position) > RESYNC:
            # Moved off the trail, by a jump or a reset: find it again from here.
            nearest = self.trail.nearest(position, zone, self.cursor)
            if nearest is not None and flat_distance(nearest.position, position) < RESYNC:
                self._reached(nearest.seq)
                next_point = nearest
        return next_point

    def _beyond(self, gate: TrailPoint) -> Point:
        """A point past *gate* the way the leader was walking into it, to walk through it."""
        before = self.trail.at(gate.seq - 1)
        if before is None or before.zone != gate.zone or flat_distance(before.position, gate.position) < 1.0:
            return gate.position
        dx, dy = gate.position[0] - before.position[0], gate.position[1] - before.position[1]
        length = math.hypot(dx, dy)
        return gate.position[0] + dx / length * GATE_PUSH, gate.position[1] + dy / length * GATE_PUSH, gate.position[2]

    def _reached(self, seq: int) -> None:
        if seq != self.cursor:
            self._stalls = 0
            self._stall_from = None
        self.cursor = seq

    def _let_go(self) -> None:
        """No longer held: back to the leader at once, by port if it's far."""
        self._held = False
        self._awaiting_move = False
        self._far_check = self._port_when_far

    @property
    def held(self) -> bool:
        """Whether it stands because it's held, as far as its last step knows."""
        return self._held

    @property
    def walking(self) -> bool:
        """Whether it holds the movement lease and is moving: the speed field is its own just now."""
        return self._walking

    @property
    def own_speed(self) -> int | None:
        """The speed field's value before it was written, the game's own, or None if never written."""
        return self._ledger.own_speed(self.client)

    @property
    def awaiting_move(self) -> bool:
        """Whether it still stands because the leader hasn't moved since it began."""
        return self._awaiting_move

    @property
    def stalled(self) -> bool:
        """Whether it is stuck: it stalled on the way to its next point, or a port failed and it waits to try again."""
        return self._stalls > 0 or self._clock() < self._port_after

    def _leader_has_moved(self, leader: Snapshot) -> bool:
        """Whether the leader has passed STILL, changed zone or entered a fight since it was first read.

        Once it has, the wait is over for good: a leader standing still later is just still.
        """
        if self._leader_from is None:
            self._leader_from = leader
        start = self._leader_from
        moved = leader.in_battle or leader.zone != start.zone or flat_distance(leader.position, start.position) > STILL
        if moved:
            self._awaiting_move = False
        return moved

    def _note_leader(self, leader: Snapshot, now: float) -> None:
        if self._leader_still_at is None or flat_distance(leader.position, self._leader_still_at[0]) > STILL:
            self._leader_still_at = (leader.position, now)

    def _leader_still(self, now: float) -> bool:
        return self._leader_still_at is not None and now - self._leader_still_at[1] >= LEADER_SETTLE

    # -- doing ------------------------------------------------------------------------------

    async def run(self) -> None:
        """Follow until cancelled or revoked. Holds the movement lease only while moving."""
        while True:
            step = await self._next_step()
            if step.kind in (StepKind.WAIT, StepKind.STOP):
                await asyncio.sleep(FOLLOW_TICK)
                continue
            try:
                async with self.arbiter.acquire(self.client, Owner.MOVEMENT, detail=self.label) as lease:
                    self._walking = True
                    try:
                        await self._drive(lease, step)
                    finally:
                        self._walking = False
            except Preempted:
                await self.arbiter.wait_free(self.client)
            except Revoked:
                return

    async def _read_position(self) -> None:
        try:
            pos = await self.client.body.position()
            self.position = (pos.x, pos.y, pos.z)
        except Exception:
            self.position = None

    async def _next_step(self) -> Step:
        await self._read_position()
        self.last_step = self.plan(self.me.latest, self.position, self.leader.latest, self._clock())
        return self.last_step

    async def _drive(self, lease: Lease, step: Step) -> None:
        keys = lease.input
        while step.kind not in (StepKind.WAIT, StepKind.STOP):
            if step.kind is StepKind.WALK:
                await self._walk(lease, step)
            elif step.kind is StepKind.INTERACT:
                await keys.release(GameAction.MOVE_FORWARD)
                await self._interact(lease, step)
            elif step.kind is StepKind.PORT:
                await keys.release_all()
                await self._port(lease, step.reason)
            await asyncio.sleep(FOLLOW_TICK)
            step = await self._next_step()
        await keys.release_all()
        if self._walking_speed is not None and self._walking_speed() is None:
            # Standing, with no toggle to hold its field: its own speed back, not the walk's.
            await self.restore_speed(lease)

    async def _walk(self, lease: Lease, step: Step) -> None:
        keys = lease.input
        await self._set_speed(lease, fast=step.fast)
        await keys.write_yaw(heading_to(self.position, step.target))
        if await keys.hold(GameAction.MOVE_FORWARD):
            self._stall_from = (self.position, self._clock())
            return
        if self._stall_from is None:
            self._stall_from = (self.position, self._clock())
            return
        start, since = self._stall_from
        if self._clock() - since < FOLLOW_STALL_WINDOW:
            return
        if flat_distance(start, self.position) < STALL_MIN_MOVE:
            self._stalls += 1
            logger.info("%s stalled following (%d of %d); backing off", self.label, self._stalls, MAX_STALLS)
            await keys.release(GameAction.MOVE_FORWARD)
            await keys.hold(GameAction.MOVE_BACKWARD)
            await asyncio.sleep(FOLLOW_BACK_OFF)
            await keys.release(GameAction.MOVE_BACKWARD)
        self._stall_from = None

    async def _interact(self, lease: Lease, step: Step) -> None:
        """Press Interact at a jump the leader took, and wait to be moved."""
        start = self.position
        deadline = self._clock() + FOLLOW_PROMPT_WAIT
        while self._clock() < deadline:
            try:
                if await self.client.is_in_npc_range():
                    break
            except Exception:
                pass
            await asyncio.sleep(FOLLOW_TICK)
        await lease.input.press(GameAction.INTERACT)
        deadline = self._clock() + FOLLOW_LINK_WAIT
        while self._clock() < deadline:
            await asyncio.sleep(FOLLOW_TICK)
            await self._read_position()
            if self.position is not None and flat_distance(self.position, start) > LINK_MOVED:
                self._reached(step.point.seq + 1)
                return
            if self.me.latest.loading:
                return
        logger.info("%s was not moved at a jump the leader took", self.label)
        self._jump_failed = step.point.seq

    async def _port(self, lease: Lease, reason: str) -> None:
        if self.port is None:
            logger.info("%s cannot keep up (%s), and has no port to fall back on", self.label, reason)
            self._port_after = self._clock() + FOLLOW_PORT_RETRY
            return
        logger.info("%s ports to its leader: %s", self.label, reason)
        ok = False
        try:
            ok = await self.port(lease)
        except (Preempted, Revoked, asyncio.CancelledError):
            raise
        except Exception:
            logger.exception("%s could not port to its leader", self.label)
        if ok:
            self.cursor = None
            self._far_check = False
            self._stalls = 0
            self._jump_failed = None
            self._gate_since = None
        else:
            self._port_after = self._clock() + FOLLOW_PORT_RETRY

    async def _set_speed(self, lease: Lease, *, fast: bool) -> None:
        """2.0x to catch up, else the walking speed it's given, or the leader's own. Never above 2.0x."""
        want = SPEED_VALUE_MAX if fast else None
        if want is None and self._walking_speed is not None:
            want = self._walking_speed()
        if want is None:
            want = self.leader.latest.speed
        if want is None:
            return
        want = min(want, SPEED_VALUE_MAX)
        try:
            current = await self.client.client_object.speed_multiplier()
        except Exception:
            return
        if current == want:
            # Nothing to write, so no walk of the UI tree on this tick.
            self._speed_watch.forget(self.client)
            return
        # The field of a logged-out character is freed memory (actions/live_character.py).
        character = await self._speed_watch.steady_character(self.client)
        if character == NO_CHARACTER:
            return
        # Anything but a value one of us wrote -- a zone change, a mount -- is the game's own.
        self._ledger.observe(self.client, character, current)
        await lease.input.write_speed(want)
        self._ledger.wrote(self.client, character, want)

    async def restore_speed(self, lease: Lease) -> None:
        """Give the follower its own speed back, if we changed it and the game has not since."""
        try:
            character = await live_character_id(self.client)
            if not self._ledger.wrote_to(self.client, character):
                self._ledger.forget(self.client)
                return
            current = await self.client.client_object.speed_multiplier()
            value = self._ledger.to_give_back(self.client, character, current)
            if value is not None:
                await lease.input.write_speed(value)
        except Exception:
            logger.debug("Could not restore %s's speed", self.label)
