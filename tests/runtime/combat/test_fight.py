"""Tests for running a fight and fleeing an unwinnable one (BartleBot #111): FightWatch, run_fight and _flee.

The watch decides per fight, from what each of our players reports at the start of a planning
phase. These drive it with reports directly, so no card or duel memory is involved; the report
itself is covered separately against fake combat members.
"""

import asyncio
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bartleby.runtime.actions import node_helpers
from bartleby.runtime.combat import fight as combat_runner
from bartleby.runtime.combat.fight import FightEnd, FightWatch, RoundReport, run_fight
from tests.conftest import FakeClient

_ROUNDS = combat_runner.FLEE_AFTER_ROUNDS


class _Log:
    def __init__(self):
        self.lines: list[tuple[str, int]] = []

    def __call__(self, message: str, level: int = logging.INFO) -> None:
        self.lines.append((message, level))

    def warnings(self) -> list[str]:
        return [m for m, level in self.lines if level >= logging.WARNING]


def _client(gid: int) -> FakeClient:
    client = FakeClient()
    client.client_object.global_id_full = AsyncMock(return_value=gid)
    return client


async def _watch(*gids: int) -> tuple[FightWatch, list, _Log]:
    clients = [_client(gid) for gid in gids]
    log = _Log()
    watch = FightWatch({c: n for n, c in enumerate(clients, start=1)}, log)
    await watch.identify()
    return watch, clients, log


def _combat(client) -> SimpleNamespace:
    return SimpleNamespace(client=client)


def _reports(table: dict):
    """Patch the round report: *table* maps (gid, round) to (has_damage, dot) or None for unreadable.

    Each player's round is counted per call, the way consecutive planning phases arrive.
    """
    rounds: dict[int, int] = {}

    async def _report(_combat, gid, _ours):
        rounds[gid] = rounds.get(gid, 0) + 1
        entry = table.get((gid, rounds[gid]), table.get((gid, "*")))
        if entry is None:
            return None
        fight, has_damage, dot = entry
        return RoundReport(rounds[gid], frozenset(fight), has_damage, dot)

    return patch.object(combat_runner, "_round_report", side_effect=_report)


async def _round(watch: FightWatch, clients: list) -> list[bool]:
    return list(await asyncio.gather(*(watch.should_flee(_combat(c)) for c in clients)))


# ---------------------------------------------------------------------------
# FightWatch
# ---------------------------------------------------------------------------


async def test_an_unwinnable_fight_flees_every_one_of_our_players_in_it():
    watch, clients, log = await _watch(11, 22)
    fight = {11, 22}
    with _reports({(11, "*"): (fight, False, False), (22, "*"): (fight, False, False)}):
        decisions = [await _round(watch, clients) for _ in range(_ROUNDS)]

    assert decisions == [[False, False]] * (_ROUNDS - 1) + [[True, True]]
    assert log.warnings() == [
        f"P1, P2: no castable damage card and no damage over time on the enemies, and no card used, for {_ROUNDS} rounds; fleeing"
    ]


async def test_one_player_with_damage_left_carries_the_fight_and_restarts_the_count():
    watch, clients, _log = await _watch(11, 22)
    fight = {11, 22}
    table = {(11, "*"): (fight, False, False), (22, "*"): (fight, False, False), (22, 2): (fight, True, False)}
    with _reports(table):
        decisions = [await _round(watch, clients) for _ in range(_ROUNDS + 2)]

    # Round 2 had damage, so the run of hopeless rounds starts again at round 3.
    assert [any(d) for d in decisions] == [False] * (_ROUNDS + 1) + [True]


async def test_damage_over_time_on_the_enemies_keeps_the_fight_going():
    watch, clients, _log = await _watch(11)
    with _reports({(11, "*"): ({11}, False, True)}):
        decisions = [await _round(watch, clients) for _ in range(_ROUNDS + 1)]

    assert not any(d[0] for d in decisions)


async def test_two_fights_are_judged_apart():
    """P1 is alone and out of damage; P2 is in another duel with damage to spare."""
    watch, clients, _log = await _watch(11, 22)
    with _reports({(11, "*"): ({11}, False, False), (22, "*"): ({22}, True, False)}):
        decisions = [await _round(watch, clients) for _ in range(_ROUNDS)]

    assert decisions[-1] == [True, False]


async def test_a_player_whose_report_never_arrives_counts_as_able_to_deal_damage(monkeypatch):
    monkeypatch.setattr(combat_runner, "FLEE_VOTE_TIMEOUT", 0.05)
    watch, clients, _log = await _watch(11, 22)
    with _reports({(11, "*"): ({11, 22}, False, False)}):  # P2's report is unreadable every round
        decisions = [await _round(watch, clients) for _ in range(_ROUNDS)]

    assert not any(any(d) for d in decisions)


@pytest.mark.parametrize("abstainer_first", [False, True])
async def test_a_player_whose_report_is_unreadable_abstains_so_no_one_waits_for_it(abstainer_first, monkeypatch):
    """P2 cannot read its report. Silent, it kept P1 waiting FLEE_VOTE_TIMEOUT every round."""
    monkeypatch.setattr(combat_runner, "FLEE_VOTE_TIMEOUT", 30.0)
    watch, clients, _log = await _watch(11, 22)
    order = list(reversed(clients)) if abstainer_first else clients
    with (
        _reports({(11, "*"): ({11, 22}, False, False)}),
        patch.object(combat_runner, "_readable_round", AsyncMock(side_effect=[1, 2, 3, 4, 5, 6])),
    ):
        decisions = [await asyncio.wait_for(_round(watch, order), 1.0) for _ in range(_ROUNDS)]

    # An abstention counts as able to deal damage, so the fight is never judged hopeless.
    assert not any(any(d) for d in decisions)


async def test_an_abstention_with_no_round_still_releases_the_open_ballot(monkeypatch):
    monkeypatch.setattr(combat_runner, "FLEE_VOTE_TIMEOUT", 30.0)
    watch, clients, _log = await _watch(11, 22)
    with (
        _reports({(11, "*"): ({11, 22}, False, False)}),
        patch.object(combat_runner, "_readable_round", AsyncMock(return_value=None)),
    ):
        assert await asyncio.wait_for(_round(watch, clients), 1.0) == [False, False]


async def test_an_unreadable_id_turns_off_the_judgement_but_not_the_time_limit():
    log = _Log()
    client = FakeClient()
    client.client_object.global_id_full = AsyncMock(side_effect=RuntimeError("unreadable"))
    watch = FightWatch({client: 1}, log)
    await watch.identify()
    with _reports({(11, "*"): ({11}, False, False)}) as report:
        assert await watch.should_flee(_combat(client)) is False
        report.assert_not_called()

        watch.flee_all("The fight ran past its time limit")
        assert await watch.should_flee(_combat(client)) is True
    assert log.warnings() == ["The fight ran past its time limit; fleeing"]


async def test_flee_all_needs_no_vote():
    watch, clients, _log = await _watch(11, 22)
    watch.flee_all("The fight ran past its time limit")
    with _reports({}) as report:
        assert await _round(watch, clients) == [True, True]
    report.assert_not_called()


# --- A fight with nothing to hit with that is not lost yet ------------------------------


async def _phases(watch: FightWatch, clients: list, rounds: int) -> list[bool]:
    """Whether the team fled at each of *rounds* planning phases with no damage in them."""
    fight = {await c.client_object.global_id_full() for c in clients}
    with _reports({(gid, "*"): (fight, False, False) for gid in fight}):
        return [any(await _round(watch, clients)) for _ in range(rounds)]


async def test_no_round_counts_until_past_every_numbered_line():
    """P2's hit is on {12}: nothing before round 13 counts, though everyone passes to get there."""
    watch, (p1, p2), log = await _watch(11, 22)
    watch.note_combat(p1, 3)
    watch.note_combat(p2, 12)

    fled = await _phases(watch, [p1, p2], 12 + _ROUNDS)

    assert fled == [False] * (12 + _ROUNDS - 1) + [True]
    assert (
        "P1, P2: no castable damage card and no damage over time on the enemies"
        " (round 12, not counted: the strategies have numbered lines through round 12)",
        logging.DEBUG,
    ) in log.lines


async def test_the_numbered_lines_of_another_fight_hold_nothing_back():
    watch, (p1, p2), _log = await _watch(11, 22)
    watch.note_combat(p2, 12)
    with _reports({(11, "*"): ({11}, False, False), (22, "*"): ({22}, False, False)}):
        decisions = [await _round(watch, [p1, p2]) for _ in range(_ROUNDS)]

    assert decisions[-1] == [True, False]


async def test_a_card_used_by_any_player_restarts_the_count():
    """A large deck can cast other things for many turns before it draws a hit."""
    watch, (p1, p2), log = await _watch(11, 22)
    fight = {11, 22}
    with _reports({(11, "*"): (fight, False, False), (22, "*"): (fight, False, False)}):
        for _ in range(_ROUNDS * 2):
            watch.note_turn(p2, True)
            assert await _round(watch, [p1, p2]) == [False, False]
        watch.note_turn(p2, False)
        decisions = [await _round(watch, [p1, p2]) for _ in range(_ROUNDS)]

    assert decisions == [[False, False]] * (_ROUNDS - 1) + [[True, True]]
    assert any("not counted: P2 used a card last turn" in message for message, _level in log.lines)


async def test_a_new_combat_forgets_the_last_turn_of_the_one_before():
    watch, (client,), _log = await _watch(11)
    watch.note_turn(client, True)
    watch.note_combat(client, 0)

    assert await _phases(watch, [client], _ROUNDS) == [False] * (_ROUNDS - 1) + [True]


@pytest.mark.parametrize(
    "strategy, last",
    [("pass", 0), ("\n".join(["{1} pass", "{12} pass", "{3} pass", "pass"]), 12)],
    ids=["unnumbered", "numbered"],
)
async def test_a_players_combat_tells_the_watch_how_far_its_strategy_is_numbered(strategy, last):
    from wizwalker.extensions.wizsprinter import SprintyCombat

    client = FakeClient()
    watch = FightWatch({client: 1}, _Log())
    watch.note_combat = MagicMock()
    with patch.object(SprintyCombat, "wait_for_combat", AsyncMock()):
        await combat_runner._run_combat(client, strategy, watch, number=1)

    watch.note_combat.assert_called_once_with(client, last)


@pytest.mark.parametrize("acts", [False, True])
async def test_the_watched_combat_tells_the_watch_what_its_turn_did(acts):
    from wizwalker.extensions.wizsprinter import SprintyCombat

    client = FakeClient()
    watch = FightWatch({client: 1}, _Log())
    watch.should_flee = AsyncMock(return_value=False)
    watch.note_turn = MagicMock()
    combat = combat_runner._watched_combat_class()(client, MagicMock(), watch)

    async def play(self):
        self.turn_acted = acts

    with patch.object(SprintyCombat, "handle_round", play):
        await combat.handle_round()

    watch.note_turn.assert_called_once_with(client, acts)


async def test_a_turn_played_by_hand_counts_as_one_that_used_a_card():
    client = FakeClient()
    watch = FightWatch({client: 1}, _Log(), casts=lambda _client: False)
    watch.note_turn = MagicMock()
    combat = combat_runner._watched_combat_class()(client, MagicMock(), watch)

    await combat.handle_round()

    watch.note_turn.assert_called_once_with(client, True)


# ---------------------------------------------------------------------------
# The round report
# ---------------------------------------------------------------------------


def _member(owner: int, team: int, *, dead: bool = False, effects=()) -> MagicMock:
    """One of the duel's participants."""
    participant = MagicMock()
    participant.owner_id_full = AsyncMock(return_value=owner)
    participant.team_id = AsyncMock(return_value=team)
    participant.game_stats = AsyncMock(
        return_value=SimpleNamespace(current_hitpoints=AsyncMock(return_value=0 if dead else 4000))
    )
    participant.hanging_effects = AsyncMock(
        return_value=[SimpleNamespace(effect_type=AsyncMock(return_value=e)) for e in effects]
    )
    return participant


def _report_combat(members: list, round_num: int = 4) -> SimpleNamespace:
    """A combat whose duel lists *members*. The combatant windows are never read."""
    client = FakeClient()
    client.duel.round_num = AsyncMock(return_value=round_num)
    client.duel.participant_list = AsyncMock(return_value=members)
    return SimpleNamespace(client=client, get_members=AsyncMock(side_effect=AssertionError("windows read")))


async def test_the_report_names_our_players_in_the_duel_and_reads_the_enemies_effects():
    from wizwalker.memory import SpellEffects

    members = [
        _member(11, team=1),
        _member(22, team=1),
        _member(99, team=1),  # someone else's wizard: in the duel, but not ours
        _member(500, team=2, effects=[SpellEffects.modify_incoming_damage, SpellEffects.damage_over_time]),
    ]
    with patch.object(combat_runner, "_holds_castable_damage", AsyncMock(return_value=True)):
        report = await combat_runner._round_report(_report_combat(members), 11, frozenset({11, 22, 33}))

    assert report == RoundReport(4, frozenset({11, 22}), True, True)


async def test_a_defeated_player_reports_no_damage_whatever_it_holds():
    members = [_member(11, team=1, dead=True), _member(500, team=2)]
    with patch.object(combat_runner, "_holds_castable_damage", AsyncMock(return_value=True)):
        report = await combat_runner._round_report(_report_combat(members), 11, frozenset({11}))

    assert report.has_damage is False
    assert report.dot_on_enemies is False


async def test_a_report_that_cannot_find_its_own_player_is_unreadable(monkeypatch):
    monkeypatch.setattr(combat_runner, "ROUND_REPORT_RETRY_INTERVAL", 0)
    members = [_member(22, team=1), _member(500, team=2)]
    assert await combat_runner._round_report(_report_combat(members), 11, frozenset({11, 22})) is None


async def test_a_report_caught_mid_rebuild_is_read_again(monkeypatch):
    """The first read of a planning phase can find a combatant mid-rebuild (MemoryInvalidated)."""
    from wizwalker.errors import MemoryInvalidated

    monkeypatch.setattr(combat_runner, "ROUND_REPORT_RETRY_INTERVAL", 0)
    members = [_member(11, team=1), _member(500, team=2)]
    combat = _report_combat(members)
    combat.client.duel.participant_list = AsyncMock(side_effect=[MemoryInvalidated("rebuilt"), members])
    with patch.object(combat_runner, "_holds_castable_damage", AsyncMock(return_value=True)):
        report = await combat_runner._round_report(combat, 11, frozenset({11}))

    assert report == RoundReport(4, frozenset({11}), True, False)


async def test_only_castable_damage_cards_count():
    heal, hit = object(), object()
    combat = SimpleNamespace(get_castable_cards=AsyncMock(return_value=[heal]))

    async def _is_damage(card, _template):
        return card is hit

    with patch("wizwalker.extensions.wizsprinter.sprinty_combat.does_card_contain_reqs", side_effect=_is_damage):
        assert await combat_runner._holds_castable_damage(combat) is False
        combat.get_castable_cards.return_value = [heal, hit]
        assert await combat_runner._holds_castable_damage(combat) is True


# ---------------------------------------------------------------------------
# Clicking Flee
# ---------------------------------------------------------------------------


@pytest.fixture
def ui(monkeypatch):
    """The two UI reads/clicks _flee uses, with the confirmation showing once Flee is clicked."""
    from bartleby.runtime.paths import FLEE_BUTTON_PATH, FLEE_CONFIRM_PATH, FLEE_CONFIRM_WINDOW_PATH

    state = SimpleNamespace(clicks=[], flee_clickable=True, confirm_appears=True, confirm_drops=0)

    async def click(_client, path):
        if path == FLEE_BUTTON_PATH and not state.flee_clickable:
            return False
        state.clicks.append("flee" if path == FLEE_BUTTON_PATH else "confirm")
        if path == FLEE_CONFIRM_PATH and state.confirm_drops:
            state.confirm_drops -= 1
            state.clicks[-1] = "confirm (dropped)"
        return True

    def confirm_up() -> bool:
        return state.confirm_appears and "flee" in state.clicks and "confirm" not in state.clicks

    async def visible(_client, path):
        # The button keeps reading visible once its window has closed; the window does not.
        if path == FLEE_CONFIRM_PATH:
            return state.confirm_appears and "flee" in state.clicks
        if path == FLEE_CONFIRM_WINDOW_PATH:
            return confirm_up()
        return False

    monkeypatch.setattr(node_helpers, "click_by_path", click)
    monkeypatch.setattr(node_helpers, "is_visible_by_path", visible)
    return state


async def test_fleeing_clicks_flee_then_confirms(ui):
    watch, (client,), log = await _watch(11)
    await combat_runner._flee(_combat(client), watch)

    assert ui.clicks == ["flee", "confirm"]
    assert watch.fled == {1}
    assert ("P1: fled", logging.INFO) in log.lines


async def test_a_flee_with_no_confirmation_is_left_for_the_next_round(ui, monkeypatch):
    monkeypatch.setattr(combat_runner, "FLEE_CONFIRM_TIMEOUT", 0.05)
    ui.confirm_appears = False
    watch, (client,), _log = await _watch(11)
    await combat_runner._flee(_combat(client), watch)

    assert ui.clicks == ["flee"]
    assert watch.fled == set()


async def test_a_dropped_confirm_is_not_counted_as_a_flee(ui, monkeypatch):
    """The next planning phase clicks it again; counting it now would call a won fight fled."""
    monkeypatch.setattr(combat_runner, "FLEE_CONFIRM_TIMEOUT", 0.05)
    ui.confirm_drops = 1
    watch, (client,), log = await _watch(11)
    await combat_runner._flee(_combat(client), watch)

    assert ui.clicks == ["flee", "confirm (dropped)"]
    assert watch.fled == set()
    assert ("P1: fled", logging.INFO) not in log.lines

    await combat_runner._flee(_combat(client), watch)

    assert ui.clicks[-1] == "confirm"
    assert watch.fled == {1}


async def test_a_defeated_player_flees_from_its_defeat_panel(ui):
    ui.flee_clickable = False
    watch, (client,), _log = await _watch(11)

    async def defeated_flee():
        ui.clicks.append("flee")

    await combat_runner._flee(SimpleNamespace(client=client, flee_button=defeated_flee), watch)

    assert ui.clicks == ["flee", "confirm"]
    assert watch.fled == {1}


async def test_the_watched_combat_flees_instead_of_casting():
    from wizwalker.extensions.wizsprinter import SprintyCombat

    watch = FightWatch({}, _Log())
    watch.should_flee = AsyncMock(return_value=True)
    combat = combat_runner._watched_combat_class()(FakeClient(), MagicMock(), watch)
    with (
        patch.object(combat_runner, "_flee", AsyncMock()) as flee,
        patch.object(SprintyCombat, "handle_round", AsyncMock()) as cast,
    ):
        await combat.handle_round()
        flee.assert_awaited_once_with(combat, watch)
        cast.assert_not_awaited()

        watch.should_flee.return_value = False
        await combat.handle_round()
        cast.assert_awaited_once()


class _Hold:
    """A hold that records when it is taken and let go, around what is done under it."""

    def __init__(self, events: list):
        self.events = events

    async def __aenter__(self):
        self.events.append("held")

    async def __aexit__(self, *_exc):
        self.events.append("let go")


async def test_a_player_played_by_hand_is_not_cast_for_nor_asked_to_flee():
    from wizwalker.extensions.wizsprinter import SprintyCombat

    client = FakeClient()
    casting = {client: False}
    watch = FightWatch({client: 1}, _Log(), casts=casting.__getitem__)
    watch.should_flee = AsyncMock(return_value=False)
    combat = combat_runner._watched_combat_class()(client, MagicMock(), watch)
    with patch.object(SprintyCombat, "handle_round", AsyncMock()) as cast:
        await combat.handle_round()
        cast.assert_not_awaited()
        watch.should_flee.assert_not_awaited()

        # Handed back: cast for from the next planning phase.
        casting[client] = True
        await combat.handle_round()
        cast.assert_awaited_once()


@pytest.mark.parametrize("flees", [False, True])
async def test_a_cast_and_a_flee_are_made_under_the_hold(flees):
    from wizwalker.extensions.wizsprinter import SprintyCombat

    events: list[str] = []
    client = FakeClient()
    watch = FightWatch({client: 1}, _Log(), hold=lambda _client: _Hold(events))
    watch.should_flee = AsyncMock(return_value=flees)

    async def act(*_args):
        events.append("acted")

    combat = combat_runner._watched_combat_class()(client, MagicMock(), watch)
    with (
        patch.object(combat_runner, "_flee", side_effect=act),
        patch.object(SprintyCombat, "handle_round", side_effect=act),
    ):
        await combat.handle_round()

    assert events == ["held", "acted", "let go"]


async def test_a_change_of_hands_is_logged_from_the_round_it_takes_effect():
    log = _Log()
    client = FakeClient()
    casting = {client: True}
    watch = FightWatch({client: 2}, log, casts=casting.__getitem__)

    assert watch.casts_for(client) is True
    casting[client] = False
    assert watch.casts_for(client) is False
    assert watch.casts_for(client) is False
    casting[client] = True
    assert watch.casts_for(client) is True

    assert [m for m, _level in log.lines] == [
        "P2 is played by hand from this round",
        "P2 is cast for again from this round",
    ]


async def _watch_by_hand(by_hand: set[int], *gids: int) -> tuple[FightWatch, list, _Log, dict]:
    """A watch over *gids* whose casts say no to the players in *by_hand*, by gid, as the test changes it."""
    clients = [_client(gid) for gid in gids]
    gid_of = dict(zip(clients, gids))
    log = _Log()
    watch = FightWatch({c: n for n, c in enumerate(clients, start=1)}, log, casts=lambda c: gid_of[c] not in by_hand)
    await watch.identify()
    return watch, clients, log, gid_of


async def test_a_fight_with_a_player_played_by_hand_would_flee_and_waits_for_no_vote_of_theirs(monkeypatch):
    """P2 is the player's: it never votes, so P1 isn't kept waiting, and the fight isn't fled."""
    monkeypatch.setattr(combat_runner, "FLEE_VOTE_TIMEOUT", 30.0)
    by_hand = {22}
    watch, (p1, _p2), log, _gids = await _watch_by_hand(by_hand, 11, 22)
    fight = {11, 22}
    with _reports({(11, "*"): (fight, False, False)}):
        decisions = [await asyncio.wait_for(watch.should_flee(_combat(p1)), 1.0) for _ in range(_ROUNDS + 1)]

    assert decisions == [False] * (_ROUNDS + 1)
    assert watch.would_flee == {1, 2}
    assert log.warnings() == [
        f"P1: no castable damage card and no damage over time on the enemies, and no card used, for {_ROUNDS} rounds;"
        " P2 is played by hand, so the team would flee and the player decides"
    ]


async def test_a_fight_that_would_flee_is_fled_once_every_player_in_it_is_cast_for_again():
    by_hand = {22}
    watch, clients, _log, _gids = await _watch_by_hand(by_hand, 11, 22)
    phase = SimpleNamespace(round=0)

    async def report(_combat, _gid, _ours):
        return RoundReport(phase.round, frozenset({11, 22}), False, False)

    with patch.object(combat_runner, "_round_report", side_effect=report):
        for phase.round in range(1, _ROUNDS + 1):
            assert await watch.should_flee(_combat(clients[0])) is False
        by_hand.clear()
        phase.round += 1
        assert await _round(watch, clients) == [True, True]

    assert watch.would_flee == frozenset()


async def test_a_round_with_damage_ends_would_flee():
    watch, (p1, _p2), _log, _gids = await _watch_by_hand({22}, 11, 22)
    fight = {11, 22}
    with _reports({(11, "*"): (fight, False, False), (11, _ROUNDS + 1): (fight, True, False)}):
        for _ in range(_ROUNDS):
            await watch.should_flee(_combat(p1))
        assert watch.would_flee == {1, 2}
        await watch.should_flee(_combat(p1))

    assert watch.would_flee == frozenset()


async def test_the_time_limit_flees_only_a_fight_with_no_player_played_by_hand():
    """P1 and P2 share a fight with P2 played by hand; P3 is alone in another."""
    watch, (p1, _p2, p3), log, _gids = await _watch_by_hand({22}, 11, 22, 33)
    watch.flee_all("The fight ran past its time limit")
    with _reports({(11, "*"): ({11, 22}, True, False), (33, "*"): ({33}, True, False)}):
        assert await watch.should_flee(_combat(p1)) is False
        assert await watch.should_flee(_combat(p3)) is True

    assert watch.would_flee == {1, 2}
    assert log.warnings()[-1] == (
        "The fight ran past its time limit for P1, P2; P2 is played by hand, so the team would flee and the player decides"
    )


# ---------------------------------------------------------------------------
# run_fight
# ---------------------------------------------------------------------------


async def test_a_fight_that_ends_on_its_own_is_finished(monkeypatch):
    monkeypatch.setattr(combat_runner, "_run_combat", AsyncMock())
    result = await run_fight([_client(11), _client(22)], ["a", "b"], [1, 2], time_limit=None, log=_Log())

    assert result == combat_runner.FightResult(FightEnd.FINISHED, [])


async def test_the_time_limit_calls_for_a_flee_and_the_fight_ends_fled(monkeypatch):
    async def fight(client, _dsl, watch, number=None):
        # Stands in for SprintyCombat: flees at the first planning phase that says to.
        while not await watch.should_flee(_combat(client)):
            await asyncio.sleep(0.001)
        watch.note_fled(client)

    monkeypatch.setattr(combat_runner, "_run_combat", fight)
    log = _Log()
    with _reports({(11, "*"): ({11}, True, False)}):
        result = await run_fight([_client(11)], ["a"], [1], time_limit=0.05, log=log)

    assert result.end is FightEnd.FLED
    assert log.warnings() == ["The fight ran past its time limit; fleeing"]


async def test_a_fight_that_outlasts_the_flee_is_cancelled_as_stuck(monkeypatch):
    monkeypatch.setattr(combat_runner, "FLEE_TIMEOUT", 0.05)
    cancelled = asyncio.Event()

    async def fight(_client, _dsl, _watch, number=None):
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    monkeypatch.setattr(combat_runner, "_run_combat", fight)
    result = await run_fight([_client(11)], ["a"], [1], time_limit=0.05, log=_Log())

    assert result.end is FightEnd.STUCK
    assert cancelled.is_set()


async def test_each_player_fights_under_its_own_number(monkeypatch):
    numbers: dict[str, int] = {}

    async def fight(_client, dsl, _watch, number=None):
        numbers[dsl] = number

    monkeypatch.setattr(combat_runner, "_run_combat", fight)
    await run_fight([_client(11), _client(22)], ["a", "b"], [3, 1], time_limit=None, log=_Log())

    assert numbers == {"a": 3, "b": 1}


@pytest.mark.parametrize("watched", [False, True])
async def test_the_player_number_tags_the_turn_trace(watched):
    from wizwalker.extensions.wizsprinter import SprintyCombat

    tags: list[str] = []

    async def wait_for_combat(self):
        tags.append(self.log_tag)

    watch = SimpleNamespace(note_combat=lambda *_: None) if watched else None
    with patch.object(SprintyCombat, "wait_for_combat", wait_for_combat):
        await combat_runner._run_combat(FakeClient(), "pass", watch, number=2)
        await combat_runner._run_combat(FakeClient(), "pass", watch)

    assert tags == ["[P2]", ""]


async def test_one_player_raising_does_not_stop_the_others(monkeypatch):
    finished: list[str] = []

    async def fight(_client, dsl, _watch, number=None):
        if dsl == "boom":
            raise RuntimeError("lost the duel window")
        await asyncio.sleep(0.01)
        finished.append(dsl)

    monkeypatch.setattr(combat_runner, "_run_combat", fight)
    result = await run_fight([_client(11), _client(22)], ["boom", "ok"], [1, 2], time_limit=None, log=_Log())

    assert result.end is FightEnd.FINISHED
    assert finished == ["ok"]
    assert [(n, str(e)) for n, e in result.errors] == [(1, "lost the duel window")]


async def test_a_run_cancelled_before_its_result_leaves_no_unretrieved_error(monkeypatch):
    """BartleBot's crash recovery cancels the node whose player died; its error was logged as an ERROR."""
    import gc

    unretrieved: list[dict] = []
    loop = asyncio.get_running_loop()
    previous = loop.get_exception_handler()
    loop.set_exception_handler(lambda _loop, context: unretrieved.append(context))

    async def fight(_client, dsl, _watch, number=None):
        if dsl == "dead":
            raise RuntimeError("Dynamic object DynamicClientDuelManager passed 0 base address.")
        await asyncio.sleep(10)

    monkeypatch.setattr(combat_runner, "_run_combat", fight)
    try:
        fighting = asyncio.create_task(
            run_fight([_client(11), _client(22)], ["dead", "alive"], [1, 2], time_limit=None, log=_Log())
        )
        await asyncio.sleep(0.01)
        fighting.cancel()
        with pytest.raises(asyncio.CancelledError):
            await fighting
        del fighting
        gc.collect()
        await asyncio.sleep(0)
    finally:
        loop.set_exception_handler(previous)

    assert unretrieved == []


# --- The team's order: what wizsprinter counts ally(-N) through ------------------------


async def test_the_duels_order_becomes_the_players_ids_in_that_order():
    watch, _clients, _log = await _watch(11, 22, 33, 44)
    watch.order = (4, 3, 2, 1)

    assert watch.team_order() == [44, 33, 22, 11]


async def test_with_no_order_the_seats_decide():
    watch, _clients, _log = await _watch(11, 22)

    assert watch.team_order() is None


async def test_a_number_in_the_order_no_player_has_is_left_out():
    watch, _clients, _log = await _watch(11, 22, 33)
    watch.order = (4, 3, 2, 1)

    assert watch.team_order() == [33, 22, 11]


async def test_an_unreadable_id_leaves_the_order_to_the_seats():
    good, bad = _client(11), FakeClient()
    bad.client_object.global_id_full = AsyncMock(side_effect=OSError("gone"))
    watch = FightWatch({good: 1, bad: 2}, _Log(), order=(2, 1))
    await watch.identify()

    assert watch.team_order() is None


async def test_each_round_hands_the_order_to_the_combat_before_it_casts():
    from wizwalker.extensions.wizsprinter import SprintyCombat

    p1, p2 = _client(11), _client(22)
    run = combat_runner.CombatRun({p1: 1, p2: 2}, _Log(), None)
    await run.identify()
    run.watch.should_flee = AsyncMock(return_value=False)
    combat = combat_runner._watched_combat_class()(p1, MagicMock(), run.watch)
    seen = []

    async def cast(self):
        seen.append(self.team_order)

    with patch.object(SprintyCombat, "handle_round", cast):
        await combat.handle_round()
        # The duel is known once the fight has begun: from the next round on, it counts.
        run.order = [2, 1]
        await combat.handle_round()

    assert seen == [None, [22, 11]]


async def test_run_fight_hands_its_order_to_the_watch(monkeypatch):
    orders = []

    async def fight(_client, _dsl, watch, number=None):
        orders.append(watch.team_order())

    monkeypatch.setattr(combat_runner, "_run_combat", fight)
    await run_fight([_client(11), _client(22)], ["a", "b"], [1, 2], time_limit=None, log=_Log(), order=[2, 1])

    assert orders == [[22, 11], [22, 11]]
