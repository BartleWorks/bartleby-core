"""The multi-client helpers, as actions under the arbiter.

What BartleBot's authoring tools did with the clients to hand (``clients/authoring_tools.py``),
rebuilt so that every press goes through a lease (:mod:`bartleby.runtime.arbiter`), and so
that each product builds its own buttons from the list (:data:`HELPERS`):

* **Interact** presses each client's Interact key where its prompt shows.
* **Dialogue** presses each open dialogue on by a page, declining any side quest offered
  unless the product says otherwise
  (:func:`~bartleby.runtime.actions.node_helpers.advance_dialog_once`); :class:`AutoDialogue`
  does it as dialogue opens.
* **Speed** keeps each client's speed field at a multiplier, never above 2.0x in total, and
  gives each its own back when switched off (:class:`SpeedBoost`). BartleBot's authoring
  Speed is this class too.
* **Gather** ports every other client to the source through the friends list.
* **Group** forms one in-game group around the source (``clients/group_manager.py``).

XYZ sync is not here: it wrote positions, which the server now resets (BartleBot's
docs/WALKING.md). Following the leader's trail (:mod:`bartleby.runtime.movement.follow`)
is what replaces it.

Each helper reads first and takes a lease only for a client it has something to press on,
so a dialogue watcher polling four clients does not stop a follower on every tick. A
helper's lease is :attr:`~bartleby.runtime.arbiter.Owner.HOTKEY`, which preempts movement
and yields to combat and recovery.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum

from bartleby.runtime.actions import node_helpers
from bartleby.runtime.actions.live_character import CharacterWatch, live_character_id
from bartleby.runtime.actions.speed_ledger import SpeedLedger
from bartleby.runtime.arbiter import Arbiter, Busy, LeaseLost, Owner
from bartleby.runtime.clients import group_manager
from bartleby.runtime.game import wizard_names
from bartleby.runtime.game.key_bindings import GameAction
from bartleby.runtime.input import speed_value
from bartleby.runtime.paths import ADVANCE_DIALOG_PATH
from bartleby.runtime.timing import DIALOG_FAST_POLL_INTERVAL, POLL_INTERVAL, SPEED_REAPPLY_INTERVAL

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Member:
    """One client of the team, and the slot the user knows it by (P1, P2...)."""

    client: object
    slot: int

    @property
    def label(self) -> str:
        return f"P{self.slot}"


class Helper(StrEnum):
    """The helpers, by the id a product stores its hotkey under."""

    INTERACT = "interact"
    DIALOGUE = "dialogue"
    AUTO_DIALOGUE = "auto_dialogue"
    SPEED = "speed"
    GATHER = "gather"
    GROUP = "group"


#: Each helper's name on a button and in a settings row, in the order a bar shows them.
HELPERS: dict[Helper, str] = {
    Helper.INTERACT: "Interact",
    Helper.DIALOGUE: "Skip dialogue",
    Helper.AUTO_DIALOGUE: "Auto-dialogue",
    Helper.SPEED: "Speed",
    Helper.GATHER: "Gather",
    Helper.GROUP: "Group",
}

#: The helpers that switch on and off rather than act once.
TOGGLES = frozenset({Helper.AUTO_DIALOGUE, Helper.SPEED})


async def _each(members: Sequence[Member], work) -> list[Member]:
    """Run *work* on every member at once; the members it returned True for."""
    results = await asyncio.gather(*(work(m) for m in members), return_exceptions=True)
    done = []
    for member, result in zip(members, results):
        if isinstance(result, LeaseLost):
            logger.debug("%s: taken by a higher owner before it finished", member.label)
        elif isinstance(result, Exception):
            logger.warning("%s: %s", member.label, result)
        elif result:
            done.append(member)
    return done


# -- Interact -----------------------------------------------------------------------------


async def interact(arbiter: Arbiter, members: Sequence[Member]) -> list[Member]:
    """Press Interact on every member whose interact prompt shows. Returns those pressed."""

    async def one(member: Member) -> bool:
        if not await member.client.is_in_npc_range():
            return False
        async with arbiter.acquire(member.client, Owner.HOTKEY, detail="interact") as lease:
            await lease.input.press(GameAction.INTERACT)
        return True

    pressed = await _each(members, one)
    logger.info("Interact: pressed on %s", _labels(pressed) or "nobody (no prompt showing)")
    return pressed


# -- Dialogue -------------------------------------------------------------------------------


async def _dialogue_open(client) -> bool:
    try:
        return await node_helpers.is_visible_by_path(client, ADVANCE_DIALOG_PATH)
    except Exception:
        return False


async def advance_dialogue(
    arbiter: Arbiter, members: Sequence[Member], *, wait: bool = True, decline: bool = True
) -> list[Member]:
    """Press every open dialogue on by one page. Returns the members it pressed on.

    With *wait* False, a member held by another owner is skipped this time rather than
    waited for: what a watcher wants, since it comes round again. With *decline* off, a side
    quest offered is accepted rather than declined (``advance_dialog_once``).
    """

    async def one(member: Member) -> bool:
        if not await _dialogue_open(member.client):
            return False
        try:
            async with arbiter.acquire(member.client, Owner.HOTKEY, wait=wait, detail="dialogue") as lease:
                return await node_helpers.advance_dialog_once(lease.client, decline=decline)
        except Busy:
            return False

    return await _each(members, one)


class _Toggle:
    """A helper that runs until switched off, over whichever members a getter answers."""

    def __init__(
        self, arbiter: Arbiter, members: Callable[[], Sequence[Member]], on_change: Callable[[], None] | None = None
    ) -> None:
        self._arbiter = arbiter
        self._members = members
        self._on_change = on_change
        self._task: asyncio.Task | None = None

    @property
    def on(self) -> bool:
        return self._task is not None

    def _changed(self) -> None:
        if self._on_change:
            try:
                self._on_change()
            except Exception:
                logger.exception("%s on_change raised", type(self).__name__)

    async def set(self, on: bool) -> None:
        if on == self.on:
            return
        if on:
            self._task = asyncio.create_task(self._loop(), name=type(self).__name__)
        else:
            task, self._task = self._task, None
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
            await self._after_off()
        self._changed()

    async def toggle(self) -> None:
        await self.set(not self.on)

    async def _loop(self) -> None:
        raise NotImplementedError

    async def _after_off(self) -> None:
        pass


class AutoDialogue(_Toggle):
    """Press dialogue on as it opens, on every member the getter answers.

    *decline* says, at each page, whether a side quest offered is declined, which it is by
    default. A product with a setting for it passes the setting.
    """

    def __init__(self, arbiter, members, on_change=None, *, decline: Callable[[], bool] = lambda: True) -> None:
        super().__init__(arbiter, members, on_change)
        self._decline = decline

    async def _loop(self) -> None:
        logger.info("Auto-dialogue on: side quests are %s", "declined" if self._decline() else "accepted")
        try:
            while True:
                pressed = await advance_dialogue(
                    self._arbiter, list(self._members()), wait=False, decline=self._decline()
                )
                # Straight on to the next page while a conversation is going, as a run's watcher does.
                await asyncio.sleep(DIALOG_FAST_POLL_INTERVAL if pressed else POLL_INTERVAL)
        finally:
            logger.info("Auto-dialogue off")


# -- Speed --------------------------------------------------------------------------------------


class SpeedBoost(_Toggle):
    """Keep every member's speed field at *multiplier* (2.0x at most), and give it back after.

    The game rewrites the field on every zone change and mount, so it is checked every
    :data:`~bartleby.runtime.timing.SPEED_REAPPLY_INTERVAL` and written again; whatever the
    game wrote is the member's own, and is what switching off restores, only to the
    character it was read from. A write waits for :class:`CharacterWatch`: the field of a
    logged-out character is freed memory (:mod:`bartleby.runtime.actions.live_character`).

    Where another writer shares the field, as a walking follower does in BartleBuddy, the two
    share a *ledger* (:mod:`bartleby.runtime.actions.speed_ledger`), so neither takes the
    other's value for the game's own, and *skip* names a client the other holds just now,
    which the boost leaves alone, on and off. Switching off gives back every field the
    ledger holds a write of, the other writer's included.
    """

    def __init__(
        self,
        arbiter,
        members,
        multiplier: Callable[[], float],
        on_change=None,
        *,
        ledger: SpeedLedger | None = None,
        skip: Callable[[object], bool] = lambda _client: False,
    ) -> None:
        super().__init__(arbiter, members, on_change)
        self._multiplier = multiplier
        self._value = 0
        self._ledger = ledger if ledger is not None else SpeedLedger()
        self._skip = skip
        self._watch = CharacterWatch()

    @property
    def value(self) -> int | None:
        """The speed field's value while on (a percentage over 1.0x), else None."""
        return self._value if self.on else None

    async def _loop(self) -> None:
        self._value = speed_value(self._multiplier())
        logger.info("Speed on: %.1fx in total", 1 + self._value / 100)
        while True:
            for member in list(self._members()):
                if self._skip(member.client):
                    continue
                with contextlib.suppress(Exception):
                    await self._boost(member)
            await asyncio.sleep(SPEED_REAPPLY_INTERVAL)

    async def _boost(self, member: Member) -> None:
        client = member.client
        character = await self._watch.steady_character(client)
        if character == wizard_names.NO_CHARACTER:
            return
        current = await client.client_object.speed_multiplier()
        if current == self._value:
            return
        self._ledger.observe(client, character, current)
        try:
            async with self._arbiter.acquire(client, Owner.HOTKEY, wait=False, detail="speed") as lease:
                await lease.input.write_speed(self._value)
                self._ledger.wrote(client, character, self._value)
        except Busy:
            pass

    async def _after_off(self) -> None:
        self._watch.clear()
        for client in self._ledger.clients():
            if not self._skip(client):
                await self._give_back(client)
        logger.info("Speed off: every player walks at their own speed")

    async def restore(self, client) -> None:
        """Give *client* its own speed back, if the field still holds ours."""
        self._watch.forget(client)
        await self._give_back(client)

    async def _give_back(self, client) -> None:
        try:
            # Only to the character it was read from, and only over our own value: one the
            # game has written since is already its own.
            character = await live_character_id(client)
            if not self._ledger.wrote_to(client, character):
                self._ledger.forget(client)
                return
            value = self._ledger.to_give_back(client, character, await client.client_object.speed_multiplier())
            if value is not None:
                async with self._arbiter.acquire(client, Owner.HOTKEY, detail="speed") as lease:
                    await lease.input.write_speed(value)
        except Exception:
            logger.debug("Speed: could not restore window %s", client.window_handle)


# -- Gather and group -------------------------------------------------------------------------


@contextlib.asynccontextmanager
async def _leases(arbiter: Arbiter, members: Sequence[Member], detail: str):
    """Leases on every member at once, taken in slot order, for a helper that needs them all."""
    async with contextlib.AsyncExitStack() as stack:
        leases = [
            await stack.enter_async_context(arbiter.acquire(m.client, Owner.HOTKEY, detail=detail))
            for m in sorted(members, key=lambda m: m.slot)
        ]
        yield leases


async def gather(arbiter: Arbiter, source: Member, members: Sequence[Member]) -> list[Member]:
    """Port every member but *source* to it through the friends list. Returns those that went.

    Needs the source's wizard name, from memory or the cache; the name never reaches a log.
    """
    movers = [m for m in members if m.client is not source.client]
    if not movers:
        logger.info("Gather: there is no one else to port")
        return []
    name = wizard_names.cached_name(source.client) or await wizard_names.read_name_from_memory(source.client)
    if not name:
        logger.warning("Gather: %s's wizard name is not known yet", source.label)
        return []
    async with _leases(arbiter, movers, "gather"):
        try:
            result = await node_helpers.port_to_friend([m.client for m in movers], name.lower())
        except ValueError as e:
            logger.warning("Gather: no one could port to %s: %s", source.label, e)
            return []
    missing = set(result.missing(len(movers)))
    went = [m for i, m in enumerate(movers) if i not in missing]
    if missing:
        logger.warning("Gather: %s did not port to %s", _labels([movers[i] for i in missing]), source.label)
    logger.info("Gather: %s ported to %s", _labels(went) or "nobody", source.label)
    return went


async def group(arbiter: Arbiter, source: Member, members: Sequence[Member]) -> bool:
    """Put every member in one in-game group, *source* leading. True when all are in it.

    Every account must be friends with the leader in game; the invites go through its
    friends list, so they work across zones.
    """
    ordered = sorted(members, key=lambda m: m.slot)
    if len(ordered) < 2:
        logger.info("Group: there is no one else to group with")
        return True
    async with _leases(arbiter, ordered, "group"):
        clients = [m.client for m in ordered]
        names = await wizard_names.resolve_names(clients)
        leader = next(i for i, m in enumerate(ordered) if m.client is source.client)
        result = await group_manager.form_group(clients, names, leader_index=leader)
    if result.success:
        logger.info("Group: %s", result.summary())
    else:
        logger.warning("Group: %s", result.summary())
    return result.success


def _labels(members: Sequence[Member]) -> str:
    return ", ".join(m.label for m in members)
