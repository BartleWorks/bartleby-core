"""launch_accounts: one batch of instances, logged in and driven together.

The shape is the reason a team launch — and, since the scheduled relog work, a fleet relog's recovery — costs
roughly one launch rather than one per client. Each drive spends minutes waiting on
loading screens and character select, so a batch that advanced them in turn would give
back everything the batch is for.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bartleby.runtime.launcher import launch_profile
from bartleby.runtime.launcher.launch_profile import launch_accounts
from bartleby.runtime.launcher.login_gate import gate

ACCOUNTS = [
    ("alpha", "pw1", "a***a"),
    ("beta", "pw2", "b***a"),
    ("gamma", "pw3", "g***a"),
]


@pytest.fixture
def launcher():
    """Patch out everything that touches Windows or the game install."""
    events: list[str] = []
    settings = MagicMock(game_resolution=(1280, 720))
    with (
        patch("bartleby.runtime.launcher.launch_profile.patch_open_browser"),
        patch("bartleby.runtime.launcher.launch_profile._patch_window_config") as config,
        patch("bartleby.runtime.launcher.launch_profile.get_all_wizard_handles", return_value=[]),
        patch(
            "bartleby.runtime.launcher.launch_profile._wait_for_n_new_handles",
            AsyncMock(return_value=[11, 12, 13]),
        ),
        patch("bartleby.runtime.launcher.launch_profile._wait_for_login_screen", AsyncMock()),
        patch("bartleby.runtime.launcher.launch_profile.start_instance", lambda: events.append("start")),
        patch("bartleby.runtime.launcher.launch_profile._log_in", new_callable=AsyncMock) as login,
        patch("bartleby.runtime.launcher.launch_profile.set_window_title"),
        patch("bartleby.runtime.launcher.launch_profile.jitter", return_value=0.0),
        patch("bartleby.runtime.launcher.launch_profile.SettingsManager") as sm,
    ):
        sm.instance.return_value = settings
        yield MagicMock(events=events, config=config, login=login)


async def test_no_accounts_launches_nothing(launcher) -> None:
    assert await launch_accounts([]) == []
    assert launcher.events == []
    launcher.config.assert_not_called()


async def test_every_account_gets_an_instance_and_a_title(launcher) -> None:
    titles = await launch_accounts(ACCOUNTS)

    assert launcher.events == ["start", "start", "start"]
    assert titles == ["[a***a] Wizard101", "[b***a] Wizard101", "[g***a] Wizard101"]
    assert [call.args[1] for call in launcher.login.call_args_list] == ["alpha", "beta", "gamma"]


async def test_the_batch_shares_one_config_patch(launcher) -> None:
    """The game reads config.xml at its own startup, and there is only one of it."""
    await launch_accounts(ACCOUNTS, resolution=(800, 600))

    launcher.config.assert_called_once()
    assert launcher.config.call_args.args[:2] == (800, 600)


async def test_no_resolution_falls_back_to_the_app_setting(launcher) -> None:
    await launch_accounts(ACCOUNTS)

    assert launcher.config.call_args.args[:2] == (1280, 720)


@pytest.fixture
def profile(launcher):
    """A profile of the three accounts, at its own 1024x768, with their passwords stored."""
    settings = launch_profile.SettingsManager.instance()
    settings.launch_profiles = [{"name": "Team", "accounts": [u for u, _p, _d in ACCOUNTS], "resolution": [1024, 768]}]
    settings.accounts = [{"username": u} for u, _p, _d in ACCOUNTS]
    with patch("bartleby.runtime.launcher.launch_profile.CredentialStore") as creds:
        creds.retrieve.return_value = "pw"
        creds.display_name.side_effect = lambda u, _a: u
        yield settings


async def test_a_profile_launches_at_its_own_resolution(launcher, profile) -> None:
    await launch_profile.launch_profile("Team")

    assert launcher.config.call_args.args[:2] == (1024, 768)


async def test_a_size_handed_to_a_profile_s_launch_is_the_one_it_opens_at(launcher, profile) -> None:
    """BartleBuddy's, the size most of the team's tiles will take: the profile's own is then its home."""
    await launch_profile.launch_profile("Team", resolution=(534, 300))

    launcher.config.assert_called_once()
    assert launcher.config.call_args.args[:2] == (534, 300)


async def test_a_profile_with_no_resolution_of_its_own_launches_at_the_app_setting(launcher, profile) -> None:
    del profile.launch_profiles[0]["resolution"]

    await launch_profile.launch_profile("Team")

    assert launcher.config.call_args.args[:2] == (1280, 720)


def test_a_profile_s_resolution_is_its_own_or_none() -> None:
    assert launch_profile.profile_resolution({"resolution": [1024, 768]}) == (1024, 768)
    assert launch_profile.profile_resolution({"resolution": None}) is None
    assert launch_profile.profile_resolution({}) is None


async def test_the_drives_overlap(launcher) -> None:
    """Started as tasks, so each window advances while the others are still waiting."""
    in_flight = 0
    peak = 0

    async def _drive(title: str) -> bool:
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0.01)
        in_flight -= 1
        return True

    await launch_accounts(ACCOUNTS, drive=_drive)

    assert peak == 3


async def test_every_drive_is_awaited_before_returning(launcher) -> None:
    """A drive still running when this returns would have the caller believe a client is
    in-game while it is still on a loading screen."""
    finished: list[str] = []

    async def _drive(title: str) -> bool:
        await asyncio.sleep(0.01)
        finished.append(title)
        return True

    await launch_accounts(ACCOUNTS, drive=_drive)

    assert len(finished) == 3


# --- the login gate: no window hooked while its login is being sent -------------------


async def test_each_window_is_held_until_its_own_login_is_sent(launcher) -> None:
    """Released one by one, not when the batch is done: each drive waits on its window's hooks."""
    held: list[list[bool]] = []
    launcher.login.side_effect = lambda *_a: held.append([gate.holds(h) for h in (11, 12, 13)])

    await launch_accounts(ACCOUNTS)

    assert held == [[True, True, True], [False, True, True], [False, False, True]]
    assert not any(gate.holds(h) for h in (11, 12, 13))


async def test_every_window_is_held_while_the_batch_spawns(launcher) -> None:
    seen: list[bool] = []
    with patch("bartleby.runtime.launcher.launch_profile.start_instance", lambda: seen.append(gate.holds(999))):
        await launch_accounts(ACCOUNTS)

    assert seen == [True, True, True]
    assert not gate.holds(999)


async def test_a_batch_that_raises_lets_its_windows_go(launcher) -> None:
    launcher.login.side_effect = RuntimeError("boom")
    with pytest.raises(RuntimeError):
        await launch_accounts(ACCOUNTS)

    assert not any(gate.holds(h) for h in (11, 12, 13))
