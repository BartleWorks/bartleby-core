"""Every launcher test launches against a plain settings object of its own."""

from dataclasses import dataclass, field

import pytest

from bartleby.runtime.launcher import launch_profile


@dataclass
class FakeLaunchSettings:
    disable_quick_friend_teleport: bool = True
    game_resolution: tuple[int, int] = (800, 600)
    original_resolution: str | None = None
    original_fullscreen: int | None = None
    original_ui_size: int | None = None
    accounts: list[dict] = field(default_factory=list)
    launch_profiles: list[dict] = field(default_factory=list)
    saves: int = 0

    def save(self) -> None:
        self.saves += 1


@pytest.fixture(autouse=True)
def launch_settings(monkeypatch):
    settings = FakeLaunchSettings()
    monkeypatch.setattr(launch_profile, "_settings", settings)
    return settings
