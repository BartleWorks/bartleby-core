"""The account map: each window's title recorded, and matched to an account where it can be.

An unmatched title is the only description of the window that will still exist by the
time the client disconnects, so it is remembered whether or not it names an account.
"""

from unittest.mock import MagicMock, patch

import pytest

from bartleby.runtime.clients.client_manager import ClientManager


class TitleHarness:
    """The state ``_refresh_account_map`` reads and writes."""

    def __init__(self) -> None:
        self._hwnd_account_map: dict[int, str] = {}
        self._hwnd_titles: dict[int, str] = {}
        self._client_resolutions: dict[str, tuple[int, int]] = {}
        self._settings = MagicMock(accounts=[])


@pytest.fixture
def harness() -> TitleHarness:
    return TitleHarness()


class TestAccountMapTitleCache:
    """_refresh_account_map records every title it reads, matched or not.

    An unmatched title is the only description of the window that will still exist by the
    time the client disconnects — the window is gone by then, so it cannot be read again.
    """

    def _run(self, harness: TitleHarness, title: str, accounts: list[dict]):
        client = MagicMock(window_handle=100)
        harness._settings = MagicMock(accounts=accounts)
        with patch("bartleby.runtime.clients.client_manager.get_window_title", return_value=title):
            ClientManager._refresh_account_map(harness, [client])

    def test_unmatched_title_is_remembered(self, harness: TitleHarness) -> None:
        self._run(harness, "Wizard101", [{"username": "user1", "alias": "Jeff"}])
        assert harness._hwnd_account_map == {}, "an adopted window matches no account"
        assert harness._hwnd_titles == {100: "Wizard101"}

    def test_matched_title_is_remembered_and_mapped(self, harness: TitleHarness) -> None:
        self._run(harness, "[Jeff] Wizard101", [{"username": "user1", "alias": "Jeff"}])
        assert harness._hwnd_account_map == {100: "user1"}
        assert harness._hwnd_titles == {100: "[Jeff] Wizard101"}

    def test_unreadable_title_records_nothing(self, harness: TitleHarness) -> None:
        client = MagicMock(window_handle=100)
        with patch("bartleby.runtime.clients.client_manager.get_window_title", side_effect=OSError("gone")):
            ClientManager._refresh_account_map(harness, [client])
        assert harness._hwnd_titles == {}
