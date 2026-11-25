"""Which duel a client is in, and who is in it, so two of our clients can be told to be in one fight or in two.

Two clients share a fight when their duels have one id (:func:`duel_id`), the game's own for
the duel and the same on every client in it from the moment each is listed there. Where an id
can't be read, when each one's duel lists the other (:func:`duel_members`). Nothing else a
client reads says so: two realms hold the same fight at the same coordinates, and every wizard
in a solo zone has a fight of its own in a zone of the same name (``docs/WIZARD101.md``,
"Battles, the ghost window and wisps").

The list is read from the duel's own participant list, as the fight runner's reports are
(:mod:`bartleby.runtime.combat.fight`). For wizards the game pulls in together it lists
everyone by the duel's first planning phase, and before that may be short, so a wizard not
listed yet says nothing. A wizard joining a duel already planning can read a list that hasn't
caught up with those in it already, which is why the id comes first: in BartleBuddy on
2026-11-25 the first follower in started a fight of its own, and the team of four ran the duel
for two.
"""

from __future__ import annotations

import asyncio
import logging

from bartleby.runtime.timing import ROUND_REPORT_READS, ROUND_REPORT_RETRY_INTERVAL

logger = logging.getLogger(__name__)


async def own_id(client) -> int | None:
    """*client*'s wizard's global id, which is what a duel lists it by, or None when it can't be read."""
    try:
        return int(await client.client_object.global_id_full())
    except Exception:
        return None


async def duel_id(client) -> int | None:
    """The game's id for *client*'s duel (``Duel.m_duelID``), or None where it can't be read.

    The same on every client in that duel, however far each one's list of it has caught up:
    four clients in one fight read one id, through planning and execution, on 2026-11-25.
    wizwalker finds the client's duel by its own wizard's place in it, so the id is readable
    exactly when the client is listed there.
    """
    try:
        return await client.duel.duel_id_full() or None
    except Exception:
        return None


async def duel_members(client) -> frozenset[int] | None:
    """The global ids of everyone in *client*'s duel, its own and the enemies' included, or None when unreadable.

    A list without the client's own wizard is one read mid-rebuild, or a duel it is no longer
    in. Tried up to ROUND_REPORT_READS times.
    """
    for attempt in range(ROUND_REPORT_READS):
        if attempt:
            await asyncio.sleep(ROUND_REPORT_RETRY_INTERVAL)
        own = await own_id(client)
        if own is None:
            continue
        try:
            listed = [int(await p.owner_id_full()) for p in await client.duel.participant_list()]
        except Exception as exc:
            logger.debug("The duel's members are unreadable (%r)", exc)
            continue
        if own in listed:
            return frozenset(listed)
    return None
