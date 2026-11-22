"""Who is in a client's duel, read from its participants."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from bartleby.runtime.combat import duel_members as duel_members_module
from bartleby.runtime.combat.duel_members import duel_members, own_id

OURS = 11


@pytest.fixture(autouse=True)
def _fast(monkeypatch):
    monkeypatch.setattr(duel_members_module, "ROUND_REPORT_RETRY_INTERVAL", 0)


def _client(*lists, own=OURS) -> SimpleNamespace:
    """A client whose duel lists each of *lists* of owner ids in turn, one a read; an exception is raised."""
    reads = [
        each
        if isinstance(each, Exception)
        else [SimpleNamespace(owner_id_full=AsyncMock(return_value=o)) for o in each]
        for each in lists
    ]
    return SimpleNamespace(
        client_object=SimpleNamespace(
            global_id_full=AsyncMock(side_effect=own if isinstance(own, Exception) else [own] * 9)
        ),
        duel=SimpleNamespace(participant_list=AsyncMock(side_effect=reads)),
    )


async def test_everyone_the_duel_lists_is_in_it_the_client_and_the_enemies_included():
    assert await duel_members(_client([OURS, 12, 500])) == {OURS, 12, 500}


async def test_a_list_unreadable_or_without_the_client_is_read_again():
    assert await duel_members(_client(RuntimeError("rebuilt"), [12, 500], [OURS, 500])) == {OURS, 500}


async def test_a_duel_that_never_lists_the_client_or_never_reads_has_no_members_to_say():
    assert await duel_members(_client([12], [12], [12])) is None
    assert await duel_members(_client(*[RuntimeError("unreadable")] * 3)) is None
    assert await duel_members(_client([OURS], own=RuntimeError("unreadable"))) is None


async def test_a_wizards_own_id_is_none_when_it_cannot_be_read():
    assert await own_id(_client()) == OURS
    assert await own_id(_client(own=RuntimeError("unreadable"))) is None
