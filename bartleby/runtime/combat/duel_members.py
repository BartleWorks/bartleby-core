"""Who is in a client's duel, so two of our clients can be told to be in one fight or in two.

Two clients share a fight when each one's duel lists the other. Nothing else a client reads
says so: two realms hold the same fight at the same coordinates, and every wizard in a solo
zone has a fight of its own in a zone of the same name (``docs/WIZARD101.md``, "Battles, the
ghost window and wisps").

Read from the duel's own participant list, as the fight runner's reports are
(:mod:`bartleby.runtime.combat.fight`), which lists everyone in the duel by its first planning
phase. Before that the list may be short, so a wizard not listed yet says nothing.
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
