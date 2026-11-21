"""Running a fight: each player's combat, and fleeing a fight that cannot be won.

:func:`run_fight` fights to the end with every client it is given, each by its own DSL.
:class:`CombatRun` is the same run for a caller that starts each player as it is seen in
battle, such as a fight no node started. Either way one :class:`FightWatch` decides, at
every planning phase, whether a fight is fled, and every one of our players in it flees
together.

What the runner needs from the app that calls it is a ``log`` callable taking
``(message, level)``, so the caller decides whose logger the lines are filed under.
Which strategy each player runs, whether the app has been stopped, what a flee counts
toward and what drops a fight left are the caller's: a product resolves the DSLs before
the fight and reads the result after it. No lease is taken here, and wizsprinter's clicks
go through wizwalker's mouse as they always have.

**Who is cast for, each planning phase.** A product whose player can take a wizard
mid-fight, as BartleBuddy's can, hands :class:`CombatRun` a ``casts`` callable, asked of
each player at the start of every planning phase. A player it says no to is played by hand
that round: nothing is cast for it, and it never votes on a flee. A fight with a player
played by hand in it is never fled: once it would be, the watch says so instead
(:attr:`FightWatch.would_flee`), and the player decides. A ``hold`` callable gives the
async context a round's cast, or a flee, is made under, such as an arbiter's combat lease.
Without either, every player is cast for, under no hold, as BartleBot's are.

wizwalker and wizsprinter are imported where they are used, so importing this module
pulls in neither. Moved from BartleBot's ``nodes/combat_runner.py`` (BartleBuddy #31,
Stage 2).
"""

from __future__ import annotations

import asyncio
import contextlib
import enum
import logging
import time
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from functools import lru_cache
from typing import NamedTuple

from bartleby.runtime import run_trace
from bartleby.runtime.combat import dsl
from bartleby.runtime.timing import (
    FLEE_AFTER_ROUNDS,
    FLEE_CONFIRM_TIMEOUT,
    FLEE_TIMEOUT,
    FLEE_VOTE_TIMEOUT,
    ROUND_REPORT_READS,
    ROUND_REPORT_RETRY_INTERVAL,
)

logger = logging.getLogger(__name__)

#: Whether the runner casts for a player at this planning phase; asked of each at every one.
Casts = Callable[[object], bool]
#: The async context a player's cast, or its flee, is made under.
Hold = Callable[[object], AbstractAsyncContextManager]


def slots_in_battle(in_battle_flags: list) -> list[int]:
    """Return the 0-based client indices whose ``in_battle()`` came back True.

    ``in_battle_flags`` is the result of a ``gather(..., return_exceptions=True)``
    over every client, so entries may be True, False, or an Exception.  Callers
    need the *indices* rather than the clients themselves: a client's position in
    the full client list is its player slot, and combat DSL is resolved per slot.
    Filtering to a shorter list of clients and then indexing positionally into the
    resolved DSLs hands the wrong player's strategy to every client after the
    first gap.
    """
    return [i for i, flag in enumerate(in_battle_flags) if flag is True]


async def _run_combat(client, combat_dsl: str, watch: FightWatch | None = None, *, number: int | None = None) -> None:
    """Run SprintyCombat for a single client using an inline DSL string.

    With a *watch*, the client asks it at every planning phase whether to flee instead of
    casting. Without one it fights to the end, as BartleBot's authoring tools' test fight wants.
    *number*, the client's 1-based player number, tags its lines in the turn trace.
    """
    provider = dsl._combat_provider(combat_dsl)
    log_tag = f"[P{number}]" if number is not None else ""
    if watch is None:
        from wizwalker.extensions.wizsprinter import SprintyCombat  # noqa: PLC0415

        await SprintyCombat(client, provider, log_tag=log_tag).wait_for_combat()
    else:
        await _watched_combat_class()(client, provider, watch, log_tag=log_tag).wait_for_combat()


class FightEnd(enum.Enum):
    """How :func:`run_fight` ended."""

    FINISHED = "finished"
    """The fight ended with nobody fleeing: won, or every one of our players defeated."""
    FLED = "fled"
    """At least one of our players fled."""
    STUCK = "stuck"
    """A flee was called for at the time limit, and the fight had still not ended
    FLEE_TIMEOUT later. The combat was cancelled; the players may still be in the fight."""


class FightResult(NamedTuple):
    end: FightEnd
    errors: list[tuple[int, BaseException]]
    """(player number, what its combat raised), for every player whose combat raised."""


class RoundReport(NamedTuple):
    """What one of our players saw at the start of a planning phase."""

    round: int
    ours_here: frozenset[int]
    """The global ids of our players in its duel, its own included."""
    has_damage: bool
    """It holds a castable damage card. False while it is defeated."""
    dot_on_enemies: bool
    """Some enemy carries damage over time, or damage deferred to a later round."""


@dataclass
class _Ballot:
    """One fight's reports for one planning phase."""

    expected: frozenset[int]
    """The players it waits for: ours in the fight, less any played by hand, who never vote."""
    votes: dict[int, RoundReport] = field(default_factory=dict)
    abstained: set[int] = field(default_factory=set)
    """Players whose report could not be read. Each counts as able to deal damage."""
    complete: asyncio.Event = field(default_factory=asyncio.Event)
    settled: bool = False

    def check(self) -> None:
        if self.expected <= self.votes.keys() | self.abstained:
            self.complete.set()


class FightWatch:
    """Decides, for each fight our players are in, when to flee it.

    One is shared by every player :func:`run_fight` runs, and each asks it at the start of
    every planning phase (:meth:`should_flee`). A fight is fled by every one of our players
    in it, never by one alone: a player out of damage is carried by the rest. It is fled

    - once, for FLEE_AFTER_ROUNDS planning phases in a row, none of our players in it has held
      a castable damage card and no enemy has carried damage over time; or
    - at once, for every fight, after :meth:`flee_all`.

    Players are grouped into fights by what each one's duel lists: two of ours share a fight
    when both are participants of it. Neither zone nor duel circle can tell, since two realms
    hold the same fight at the same coordinates. Grouping needs every player's global id, so
    a watch that cannot read one of them never decides a fight is unwinnable, and only
    :meth:`flee_all` can make its players flee.

    Every doubt counts against fleeing: a report that cannot be read, or that has not arrived
    within FLEE_VOTE_TIMEOUT, is read as "can still deal damage" and breaks the run of rounds.
    A player whose own report cannot be read abstains, which says so at once: staying silent
    left everyone else waiting out the timeout for it, 5 s of every round it happened in.

    With *casts*, a player it says no to is played by hand: it is not waited for, and a fight
    with one in it is never fled, :meth:`flee_all` included. Such a fight goes into
    :attr:`would_flee` instead, until a round in it is no longer hopeless, or every player in
    it is cast for again and it is fled. *hold* is what a cast or a flee is made under.
    """

    def __init__(
        self, numbers: dict, log: Callable[..., None], *, casts: Casts | None = None, hold: Hold | None = None
    ):
        self._numbers = numbers  # client -> 1-based player number, for log lines only
        self.log = log
        self._casts = casts
        self._hold = hold
        self._gids: dict = {}  # client -> global id; empty when any was unreadable
        self._ballots: dict[tuple[int, frozenset[int]], _Ballot] = {}
        self._abstentions: dict[int, set[int]] = {}  # round -> players who abstained in it
        self._streaks: dict[frozenset[int], int] = {}
        self._fleeing: list[frozenset[int]] = []
        self._flee_all_reason: str | None = None
        self._would: dict[frozenset[int], str] = {}  # fight -> why it would be fled
        self._cast_for: dict = {}  # client -> whether its last planning phase was cast for
        self.fled: set[int] = set()  # player numbers that carried out a flee

    async def identify(self) -> None:
        """Read every player's global id, which is its combat member's owner id."""

        async def _gid(client) -> int:
            return int(await client.client_object.global_id_full())

        clients = list(self._numbers)
        gids = await asyncio.gather(*(_gid(c) for c in clients), return_exceptions=True)
        if any(isinstance(g, BaseException) for g in gids):
            self.log("Fight watch: a player's id is unreadable; only the time limit can make them flee", logging.DEBUG)
            return
        self._gids = dict(zip(clients, gids))

    def flee_all(self, reason: str) -> None:
        """Make every player flee at its next planning phase."""
        if self._flee_all_reason is None:
            self._flee_all_reason = reason
            self.log(f"{reason}; fleeing", logging.WARNING)

    def note_fled(self, client) -> None:
        self.fled.add(self._numbers[client])

    def number(self, client) -> int:
        return self._numbers[client]

    def casts_for(self, client) -> bool:
        """Whether *client* is cast for at this planning phase. Ask once per planning phase.

        A change from its last planning phase is logged: the player took the wizard, or handed it back.
        """
        casts = self._casts is None or self._casts(client)
        was = self._cast_for.get(client)
        self._cast_for[client] = casts
        if was is not None and was != casts:
            how = "is cast for again" if casts else "is played by hand"
            self.log(f"P{self._numbers[client]} {how} from this round", logging.INFO)
        return casts

    def hold_for(self, client) -> AbstractAsyncContextManager:
        """What *client*'s cast or flee is made under: the caller's *hold*, or nothing."""
        return contextlib.nullcontext() if self._hold is None else self._hold(client)

    @property
    def would_flee(self) -> frozenset[int]:
        """The player numbers in a fight that would be fled, but for a player in it played by hand."""
        return frozenset(
            self._numbers[client] for client, gid in self._gids.items() if any(gid in fight for fight in self._would)
        )

    def _by_hand(self, gids: frozenset[int]) -> frozenset[int]:
        """Those of *gids* whose player is played by hand now."""
        if self._casts is None:
            return frozenset()
        return frozenset(gid for client, gid in self._gids.items() if gid in gids and not self._casts(client))

    async def should_flee(self, combat) -> bool:
        """Whether *combat*'s player should flee now rather than cast. Call once per planning phase."""
        if self._flee_all_reason is not None and self._casts is None:
            return True
        gid = self._gids.get(combat.client)
        if gid is None:
            # No fights can be told apart: only the time limit flees, and not while anyone is played by hand.
            return self._flee_all_reason is not None and all(self._casts(client) for client in self._numbers)
        if self._flees(gid):
            return True
        report = await _round_report(combat, gid, frozenset(self._gids.values()))
        if report is None:
            if self._flee_all_reason is not None:
                return not self._by_hand(frozenset(self._gids.values()))
            self._abstain(gid, await _readable_round(combat))
            return False
        if self._flee_all_reason is not None:
            what = f"{self._flee_all_reason} for {self._players(report.ours_here)}"
            return self._flee_unless_by_hand(report.ours_here, what, announce=False)
        key = (report.round, report.ours_here)
        ballot = self._ballot(key)
        ballot.votes[gid] = report
        ballot.check()
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(ballot.complete.wait(), FLEE_VOTE_TIMEOUT)
        self._settle(key, ballot)
        return self._flees(gid)

    def _ballot(self, key: tuple[int, frozenset[int]]) -> _Ballot:
        """The ballot for *key*, made with any abstentions already cast in its round."""
        ballot = self._ballots.get(key)
        if ballot is None:
            ballot = self._ballots[key] = _Ballot(key[1] - self._by_hand(key[1]))
            ballot.abstained |= self._abstentions.get(key[0], set()) & ballot.expected
        return ballot

    def _abstain(self, gid: int, round_num: int | None) -> None:
        """Count *gid* as able to deal damage in its fight this planning phase, so no one waits for it.

        Recorded against the round, for a ballot the others have not opened yet, and cast on
        every open ballot expecting it. With the round unreadable too, on every open one.
        """
        if round_num is not None:
            self._abstentions.setdefault(round_num, set()).add(gid)
        for (ballot_round, _ours), ballot in self._ballots.items():
            if gid in ballot.expected and not ballot.settled and round_num in (None, ballot_round):
                ballot.abstained.add(gid)
                ballot.check()

    def _flees(self, gid: int) -> bool:
        return any(gid in fight for fight in self._fleeing)

    def _settle(self, key: tuple[int, frozenset[int]], ballot: _Ballot) -> None:
        """Count one planning phase towards (or against) fleeing its fight. Once per ballot."""
        if ballot.settled:
            return
        ballot.settled = True
        fight = key[1]
        hopeless = (
            ballot.complete.is_set()
            and not ballot.abstained
            and not any(vote.has_damage or vote.dot_on_enemies for vote in ballot.votes.values())
        )
        if not hopeless:
            self._streaks[fight] = 0
            self._would.pop(fight, None)
            return
        streak = self._streaks.get(fight, 0) + 1
        self._streaks[fight] = streak
        what = f"{self._players(ballot.expected)}: no castable damage card and no damage over time on the enemies"
        if streak < FLEE_AFTER_ROUNDS:
            self.log(f"{what} (round {key[0]}, {streak} of {FLEE_AFTER_ROUNDS})", logging.DEBUG)
            return
        self._flee_unless_by_hand(fight, f"{what} for {streak} rounds")

    def _flee_unless_by_hand(self, fight: frozenset[int], what: str, *, announce: bool = True) -> bool:
        """Flee *fight*, unless a player in it is played by hand: then it would be, and the player decides.

        Returns whether it is fled. *what* says why, and with *announce* a flee is logged with it.
        A fight that would be fled is logged as it first would be.
        """
        by_hand = self._by_hand(fight)
        if not by_hand:
            self._would.pop(fight, None)
            if fight not in self._fleeing:
                self._fleeing.append(fight)
                if announce:
                    self.log(f"{what}; fleeing", logging.WARNING)
            return True
        if fight not in self._would:
            verb = "is" if len(by_hand) == 1 else "are"
            self.log(
                f"{what}; {self._players(by_hand)} {verb} played by hand, so the team would flee and the player decides",
                logging.WARNING,
            )
        self._would[fight] = what
        return False

    def _players(self, gids: frozenset[int]) -> str:
        numbers = sorted(self._numbers[c] for c, gid in self._gids.items() if gid in gids)
        return ", ".join(f"P{n}" for n in numbers)


async def _round_report(combat, own_gid: int, our_gids: frozenset[int]) -> RoundReport | None:
    """What *combat*'s player sees at the start of this planning phase, or None if unreadable.

    Read from the duel's own participant list, never the combatant windows: the game rebuilds
    those at the start of every planning phase, and the first player there read 4 of a duel's
    8 (so its report named a different fight) or one mid-rebuild (MemoryInvalidated). Tried up
    to ROUND_REPORT_READS times.
    """
    for attempt in range(ROUND_REPORT_READS):
        if attempt:
            await asyncio.sleep(ROUND_REPORT_RETRY_INTERVAL)
        try:
            report = await _read_round_report(combat, own_gid, our_gids)
        except Exception as exc:
            logger.debug("Fight watch: round report unreadable (%r)", exc)
            continue
        if report is not None:
            return report
        logger.debug("Fight watch: the player is not among its duel's participants")
    return None


async def _read_round_report(combat, own_gid: int, our_gids: frozenset[int]) -> RoundReport | None:
    duel = combat.client.duel
    round_num = await duel.round_num()
    participants = [(p, int(await p.owner_id_full())) for p in await duel.participant_list()]
    me = next((p for p, owner in participants if owner == own_gid), None)
    if me is None:
        return None
    my_team = await me.team_id()
    enemies = [p for p, _owner in participants if await p.team_id() != my_team]
    defeated = await (await me.game_stats()).current_hitpoints() == 0
    has_damage = not defeated and await _holds_castable_damage(combat)
    dot_on_enemies = await _carries_damage_over_time(enemies)
    ours_here = frozenset(owner for _p, owner in participants if owner in our_gids)
    return RoundReport(round_num, ours_here, has_damage, dot_on_enemies)


async def _readable_round(combat) -> int | None:
    """The duel's round number, or None if it cannot be read."""
    try:
        return await combat.client.duel.round_num()
    except Exception:
        return None


async def _holds_castable_damage(combat) -> bool:
    """Whether the player holds a card it can cast now that damages an enemy."""
    from wizwalker.extensions.wizsprinter.combat_backends.combat_api import (  # noqa: PLC0415
        SpellType,
        TemplateSpell,
    )
    from wizwalker.extensions.wizsprinter.sprinty_combat import does_card_contain_reqs  # noqa: PLC0415

    damage = TemplateSpell([SpellType.type_damage])
    for card in await combat.get_castable_cards():
        if await does_card_contain_reqs(card, damage):
            return True
    return False


async def _carries_damage_over_time(participants: list) -> bool:
    """Whether any of *participants* has damage still to take from an effect already on it."""
    from wizwalker.memory import SpellEffects  # noqa: PLC0415

    pending = {SpellEffects.damage_over_time, SpellEffects.deferred_damage}
    for participant in participants:
        for effect in await participant.hanging_effects():
            if await effect.effect_type() in pending:
                return True
    return False


async def _flee(combat, watch: FightWatch) -> None:
    """Click Flee and confirm it. A miss is logged, and retried at the next planning phase."""
    from bartleby.runtime.actions import node_helpers  # noqa: PLC0415
    from bartleby.runtime.paths import FLEE_BUTTON_PATH, FLEE_CONFIRM_PATH, FLEE_CONFIRM_WINDOW_PATH  # noqa: PLC0415

    client = combat.client
    number = watch.number(client)
    if not await node_helpers.click_by_path(client, FLEE_BUTTON_PATH):
        # A defeated player's Flee is on its defeat panel instead, which WizWalker knows.
        try:
            async with client.mouse_handler:
                await combat.flee_button()
        except Exception as exc:
            watch.log(f"P{number}: Flee could not be clicked ({exc!r}); trying again next round", logging.DEBUG)
            return

    async def _confirm_showing() -> bool:
        return await node_helpers.is_visible_by_path(client, FLEE_CONFIRM_PATH)

    if not await node_helpers.poll_until(_confirm_showing, FLEE_CONFIRM_TIMEOUT):
        watch.log(f"P{number}: clicked Flee, but no confirmation appeared; trying again next round", logging.DEBUG)
        return
    await node_helpers.click_by_path(client, FLEE_CONFIRM_PATH)

    async def _confirm_closed() -> bool:
        return not await node_helpers.is_visible_by_path(client, FLEE_CONFIRM_WINDOW_PATH)

    # Counted only once the confirmation has gone. A dropped click leaves it up, and the next
    # planning phase finds it showing and clicks it again; counting it now would end the
    # fight as fled even if the others went on to win it.
    if not await node_helpers.poll_until(_confirm_closed, FLEE_CONFIRM_TIMEOUT):
        watch.log(f"P{number}: confirmed the flee, but the confirmation stayed; trying again next round", logging.DEBUG)
        return
    watch.note_fled(client)
    watch.log(f"P{number}: fled", logging.INFO)


@lru_cache(maxsize=1)
def _watched_combat_class():
    """SprintyCombat that asks a FightWatch, each planning phase, whether to flee instead.

    Built lazily, so importing this module does not pull in wizwalker, and cached.
    """
    from wizwalker.extensions.wizsprinter import SprintyCombat  # noqa: PLC0415

    class _WatchedCombat(SprintyCombat):
        # The run_trace spans split each round into what the game took (the waits) and
        # what we took (the round itself: each priority tried, each condition, each cast).
        def __init__(self, client, provider, watch: FightWatch, *, log_tag: str = ""):
            super().__init__(client, provider, log_tag=log_tag)
            self.watch = watch
            self._tries = 0  # priorities tried this round, for the trace

        async def handle_combat(self):
            with run_trace.span("combat.fight", client=self.client):
                await super().handle_combat()

        async def wait_for_planning_phase(self, *args, **kwargs):
            with run_trace.span("combat.wait_planning"):
                await super().wait_for_planning_phase(*args, **kwargs)

        async def wait_until_next_round(self, current_round: int, *args, **kwargs):
            with run_trace.span("combat.wait_next_round", round=current_round):
                await super().wait_until_next_round(current_round, *args, **kwargs)

        async def handle_round(self):
            with run_trace.span("combat.round"):
                if run_trace.active():
                    run_trace.note(round=await self.round_number())
                self._tries = 0
                if not self.watch.casts_for(self.client):
                    # Played by hand this round: the player casts, and the game waits for them.
                    run_trace.note(by_hand=True)
                    return
                # Outside SprintyCombat's own mouse_handler block: the flee's clicks take it themselves.
                # The vote waits on the others, so it is made before the hold is taken.
                if await self.watch.should_flee(self):
                    run_trace.note(fled=True)
                    async with self.watch.hold_for(self.client):
                        await _flee(self, self.watch)
                    return
                async with self.watch.hold_for(self.client):
                    await super().handle_round()

        async def try_execute_config(self, move_config, willcasted: bool = False):
            self._tries += 1
            with run_trace.span("combat.priority", n=self._tries):
                result = await super().try_execute_config(move_config, willcasted)
                run_trace.note(result=repr(result))
                return result

        async def evaluate_condition(self, condition) -> bool:
            with run_trace.span("combat.condition"):
                return await super().evaluate_condition(condition)

    return _WatchedCombat


def _mark_retrieved(task: asyncio.Task) -> None:
    """Mark a finished combat task's exception as seen. :meth:`CombatRun.result` still reports it.

    One player's combat raising leaves the others fighting, and its error is read once they are
    done. A run cancelled first never gets there: BartleBot's crash recovery cancels the handle_combat
    node whose player died, and asyncio then reported that player's dead-client read as "Task exception
    was never retrieved", an ERROR with a traceback for a crash recovery had already handled.
    """
    if not task.cancelled():
        task.exception()


class CombatRun:
    """Every combat task of one fight session, sharing one FightWatch.

    :func:`run_fight` starts everyone at once. BartleBot's ambient watcher starts each player as it
    is seen in battle, which is what lets a helper, or a bystander pulled into a fight of its
    own, have its combat run whenever it gets there.

    *numbers* maps every client that may fight to its 1-based player number, so the watch
    can group players by fight whichever of them turn up. The time limit runs from creation.
    *casts* and *hold* are the watch's: who is cast for at each planning phase, and what a
    cast is made under (the module's docstring).
    """

    def __init__(
        self,
        numbers: dict,
        log: Callable[..., None],
        time_limit: float | None,
        *,
        casts: Casts | None = None,
        hold: Hold | None = None,
    ):
        self.watch = FightWatch(numbers, log, casts=casts, hold=hold)
        self._numbers = numbers
        self._deadline = None if time_limit is None else time.monotonic() + time_limit
        self._tasks: list[tuple[int, asyncio.Task]] = []
        self._live: dict = {}  # client -> its latest combat task
        self.stuck = False

    async def identify(self) -> None:
        await self.watch.identify()

    def start(self, client, dsl: str) -> bool:
        """Start *client*'s combat unless it is already running. True when it was started."""
        if self.fighting(client):
            return False
        task = asyncio.create_task(_run_combat(client, dsl, self.watch, number=self._numbers[client]))
        task.add_done_callback(_mark_retrieved)
        self._live[client] = task
        self._tasks.append((self._numbers[client], task))
        return True

    def fighting(self, client) -> bool:
        task = self._live.get(client)
        return task is not None and not task.done()

    @property
    def tasks(self) -> list[asyncio.Task]:
        return [task for _number, task in self._tasks]

    @property
    def busy(self) -> bool:
        return any(not task.done() for task in self.tasks)

    def check_time_limit(self) -> bool:
        """Enforce the time limit; call it as the fight goes on. True once combat is cancelled.

        Past the limit every fight is fled; FLEE_TIMEOUT after that, combat still running is
        cancelled and the run counts as stuck.
        """
        if self._deadline is None or self.stuck:
            return self.stuck
        now = time.monotonic()
        if now >= self._deadline:
            self.watch.flee_all("The fight ran past its time limit")
        if now >= self._deadline + FLEE_TIMEOUT and self.busy:
            self.cancel()
            self.stuck = True
        return self.stuck

    def cancel(self) -> None:
        for task in self.tasks:
            if not task.done():
                task.cancel()

    def result(self) -> FightResult:
        """How the run ended. Call once every task is done."""
        errors = [
            (number, task.exception())
            for number, task in self._tasks
            if task.done() and not task.cancelled() and task.exception() is not None
        ]
        if self.stuck:
            end = FightEnd.STUCK
        elif self.watch.fled:
            end = FightEnd.FLED
        else:
            end = FightEnd.FINISHED
        return FightResult(end, errors)


async def run_fight(
    clients: list,
    dsls: list[str],
    numbers: list[int],
    *,
    time_limit: float | None,
    log: Callable[..., None],
) -> FightResult:
    """Fight to the end with every one of *clients*, fleeing any fight that cannot be won.

    ``clients``, ``dsls`` and ``numbers`` are parallel; ``numbers`` are 1-based player numbers,
    used in log lines. *time_limit* is how many seconds from now every fight is fled, or None
    for no limit; zero or less flees at the first planning phase. *log* takes ``(message,
    level)``, so the caller decides whose logger the lines are filed under.

    One player's combat raising does not stop the others; it is reported in the result.
    """
    if not clients:
        return FightResult(FightEnd.FINISHED, [])
    await dsl.parse_strategies(dsls)
    run = CombatRun(dict(zip(clients, numbers)), log, time_limit)
    await run.identify()
    for client, text in zip(clients, dsls):
        run.start(client, text)
    try:
        _done, pending = await asyncio.wait(run.tasks, timeout=time_limit)
        if pending:
            run.watch.flee_all("The fight ran past its time limit")
            _done, pending = await asyncio.wait(pending, timeout=FLEE_TIMEOUT)
        if pending:
            run.stuck = True
            run.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
    finally:
        run.cancel()  # only anything left when this was cancelled from outside
    return run.result()
