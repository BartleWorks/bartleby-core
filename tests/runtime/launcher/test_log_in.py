"""_log_in: the login sent through the client's memory, or typed when it cannot be."""

import logging
from unittest.mock import AsyncMock, patch

import pytest
from wizwalker import MemoryLoginError

from bartleby.runtime.launcher import launch_profile


@pytest.fixture
def typed():
    with patch("bartleby.runtime.launcher.launch_profile.instance_login") as typed:
        yield typed


async def test_the_login_goes_through_memory_and_is_not_typed(typed):
    with patch("bartleby.runtime.launcher.launch_profile.memory_login", new_callable=AsyncMock) as sent:
        await launch_profile._log_in(42, "someone", "hunter2!")

    assert sent.await_args.args == (42, "someone", "hunter2!")
    typed.assert_not_called()


@pytest.mark.parametrize("error", [MemoryLoginError("the login call was not found"), OSError("access denied")])
async def test_a_login_memory_cannot_send_is_typed_and_says_why(typed, caplog, error):
    with patch("bartleby.runtime.launcher.launch_profile.memory_login", AsyncMock(side_effect=error)):
        with caplog.at_level(logging.WARNING):
            await launch_profile._log_in(42, "someone", "hunter2!")

    typed.assert_called_once_with(42, "someone", "hunter2!")
    assert str(error) in caplog.text
    assert "hunter2!" not in caplog.text and "someone" not in caplog.text
