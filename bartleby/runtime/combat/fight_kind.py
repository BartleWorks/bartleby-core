"""Which kind of fight a client is in: a boss fight, or a mob fight.

A boss fight is one with any living enemy that is a boss, the rule of the shipped
strategies' ``?(any(enemies).is_boss == 1)``, so a duel picked by the kind and a strategy
that asks for itself agree. It reads correctly at the first planning phase
(``docs/WIZARD101.md``, "Seats and casting order"). Before that the enemies may not all be
listed, so a boss seen early is one, and no boss seen early says nothing.

Read from the duel's own participant list, as the fight runner's reports are
(:mod:`bartleby.runtime.combat.fight`), never from the combatant windows, which the game
rebuilds at the start of every planning phase. So it needs only a client, and no combat need
be running.
"""

from __future__ import annotations

import asyncio
import logging

from bartleby.runtime.timing import ROUND_REPORT_READS, ROUND_REPORT_RETRY_INTERVAL
from bartleby.shared.combat_defaults import FightKind

logger = logging.getLogger(__name__)


async def fight_kind(client) -> FightKind | None:
    """The kind of the fight *client* is in, or None when it can't be read or no living enemy is listed.

    Tried up to ROUND_REPORT_READS times, since a list can be read mid-rebuild.
    """
    for attempt in range(ROUND_REPORT_READS):
        if attempt:
            await asyncio.sleep(ROUND_REPORT_RETRY_INTERVAL)
        try:
            kind = await _read_kind(client)
        except Exception as exc:
            logger.debug("The fight's kind is unreadable (%r)", exc)
            continue
        if kind is not None:
            return kind
    return None


async def _read_kind(client) -> FightKind | None:
    own_gid = int(await client.client_object.global_id_full())
    participants = [(p, int(await p.owner_id_full())) for p in await client.duel.participant_list()]
    me = next((p for p, owner in participants if owner == own_gid), None)
    if me is None:
        return None
    my_team = await me.team_id()
    kind = None
    for participant, _owner in participants:
        # Health is the test for living: a killed enemy stays listed at 0 for a while.
        if await participant.team_id() == my_team or await participant.player_health() <= 0:
            continue
        if await participant.boss_mob():
            return FightKind.BOSS
        kind = FightKind.MOB
    return kind
