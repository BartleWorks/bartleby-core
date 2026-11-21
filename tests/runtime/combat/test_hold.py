"""Tests for hold.FirstCastHold: a fighter's first cast waits for the players joining its fight.

Moved from BartleBot's ambient combat tests, which drove it through ``AmbientSession._released``.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

from wizwalker.memory import DuelPhase

from bartleby.runtime.combat.hold import FirstCastHold
from bartleby.runtime.timing import AMBIENT_HELP_HOLD
from tests.conftest import FakeClient


class _Clock:
    def __init__(self, now: float = 100.0):
        self.now = now

    def __call__(self) -> float:
        return self.now


def _held(clock: _Clock, *, phase=DuelPhase.planning):
    fighter, joining = FakeClient(), FakeClient()
    fighter.duel.duel_phase = AsyncMock(return_value=phase)
    return fighter, joining, FirstCastHold([joining], started=clock.now, clock=clock)


def _waiting(_player) -> bool:
    return False


async def test_the_hold_expires_twenty_seconds_after_the_first_planning_phase():
    clock = _Clock(100.0)
    fighter, _joining, hold = _held(clock)

    assert await hold.ready(fighter, _waiting, {}) is False  # planning first seen at 100
    clock.now = 100.0 + AMBIENT_HELP_HOLD - 0.1
    assert await hold.ready(fighter, _waiting, {}) is False
    clock.now = 100.0 + AMBIENT_HELP_HOLD
    assert await hold.ready(fighter, _waiting, {}) is True


async def test_a_join_moves_the_anchor_since_the_game_restarts_its_timer():
    clock = _Clock(100.0)
    fighter, joining, hold = _held(clock)
    await hold.ready(fighter, _waiting, {})
    joined_at = {joining: 110.0}

    clock.now = 110.0 + AMBIENT_HELP_HOLD - 0.1
    assert await hold.ready(fighter, _waiting, joined_at) is False
    clock.now = 110.0 + AMBIENT_HELP_HOLD
    assert await hold.ready(fighter, _waiting, joined_at) is True


async def test_the_hold_waits_for_planning_to_start_counting():
    clock = _Clock(100.0)
    fighter, _joining, hold = _held(clock, phase=DuelPhase.execution)

    clock.now = 500.0
    assert await hold.ready(fighter, _waiting, {}) is False


async def test_an_unreadable_phase_counts_the_hold_from_when_the_fight_was_seen():
    clock = _Clock(100.0)
    fighter, _joining, hold = _held(clock)
    fighter.duel.duel_phase = AsyncMock(side_effect=RuntimeError("unreadable"))

    clock.now = 100.0 + AMBIENT_HELP_HOLD
    assert await hold.ready(fighter, _waiting, {}) is True


async def test_the_hold_ends_once_everyone_joining_is_in_or_has_given_up():
    fighter, joining, hold = _held(_Clock())

    assert await hold.ready(fighter, lambda player: player is joining, {}) is True


async def test_once_released_it_stays_released():
    """A joiner that is no longer settled, or a phase read again, never takes a cast back."""
    fighter, joining, hold = _held(_Clock())
    await hold.ready(fighter, lambda player: player is joining, {})
    fighter.duel.duel_phase = AsyncMock(side_effect=AssertionError("read again"))

    assert await hold.ready(fighter, _waiting, {}) is True


async def test_a_fighter_nobody_is_joining_casts_at_once():
    fighter = FakeClient()
    fighter.duel.duel_phase = AsyncMock(side_effect=AssertionError("no need to read it"))

    assert await FirstCastHold([], started=0.0).ready(fighter, _waiting, {}) is True
