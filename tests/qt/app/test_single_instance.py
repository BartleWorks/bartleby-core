"""One copy of an app per user: a later launch hands its arguments over and exits (BartleBot #27)."""

from __future__ import annotations

import json
import subprocess
import sys
import uuid
from dataclasses import replace

import pytest
from PyQt6.QtNetwork import QLocalSocket
from PyQt6.QtWidgets import QWidget

from bartleby.qt.app import single_instance
from bartleby.qt.app.single_instance import LaunchQueue, SingleInstance, server_name


@pytest.fixture
def name():
    return f"Bartleby.Sample.test-{uuid.uuid4().hex}"


#: A later launch, as an app's entry point makes one: its own process, exiting 3 when it handed over.
_LATER_LAUNCH = """
import sys
from pathlib import Path
from PyQt6.QtCore import QCoreApplication
from bartleby.qt.app.single_instance import SingleInstance
from bartleby.shared.product import Product
product = Product(name="Sample", version="1.2.3", accent="#3366bb", accent_dark="#5b95d6", logo=Path("logo.svg"))
app = QCoreApplication(sys.argv[:1])
sys.exit(0 if SingleInstance(product, name=sys.argv[1]).claim(sys.argv[2:]) else 3)
"""


def _later_launch(name: str, *args: str, cwd=None) -> subprocess.Popen:
    """Start a later launch in its own process; the running one must keep its loop turning meanwhile."""
    return subprocess.Popen([sys.executable, "-c", _LATER_LAUNCH, name, *args], cwd=cwd)


def _handed_over(qtbot, launch: subprocess.Popen) -> bool:
    qtbot.waitUntil(lambda: launch.poll() is not None, timeout=30000)
    return launch.returncode == 3


@pytest.fixture
def installed(sample_product):
    return replace(sample_product, build_kind="packaged (PyInstaller)")


@pytest.fixture
def first(qapp, name, sample_product):
    instance = SingleInstance(sample_product, name=name)
    yield instance
    instance.close()


def test_the_first_launch_runs(first):
    assert first.claim([]) is True
    assert first.is_listening


def test_a_later_launch_hands_its_arguments_over_and_does_not_run(qtbot, first, name, tmp_path):
    share = tmp_path / "Gift.bbot"
    share.write_text("{}", encoding="utf-8")
    first.claim([])

    with qtbot.waitSignal(first.arguments_received, timeout=30000) as received:
        launch = _later_launch(name, "Gift.bbot", "--flag", cwd=tmp_path)
    assert _handed_over(qtbot, launch)

    assert received.args == [[str(share.resolve()), "--flag"]]


def test_a_relative_path_is_made_absolute_before_it_is_sent(monkeypatch, tmp_path):
    (tmp_path / "Gift.bbot").write_text("{}", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    assert single_instance.absolute_arguments(["Gift.bbot", "--x"]) == [str(tmp_path / "Gift.bbot"), "--x"]


def test_two_later_launches_each_arrive(qtbot, first, name):
    first.claim([])
    got = []
    first.arguments_received.connect(got.append)

    launches = [_later_launch(name, "a"), _later_launch(name, "b")]
    assert all(_handed_over(qtbot, launch) for launch in launches)
    qtbot.waitUntil(lambda: len(got) == 2, timeout=5000)

    assert sorted(got) == [["a"], ["b"]]


@pytest.mark.parametrize("payload", [b"not json", json.dumps({"a": 1}).encode(), json.dumps([1, 2]).encode()])
def test_something_other_than_arguments_is_ignored(qtbot, first, name, payload):
    first.claim([])
    got = []
    first.arguments_received.connect(got.append)
    socket = QLocalSocket()
    socket.connectToServer(name)
    assert socket.waitForConnected(2000)
    socket.write(payload)
    socket.flush()
    qtbot.wait(50)
    socket.disconnectFromServer()
    qtbot.wait(200)

    assert got == []


def test_once_the_first_has_closed_the_next_launch_runs(first, name, sample_product):
    first.claim([])
    first.close()

    second = SingleInstance(sample_product, name=name)
    assert second.claim([]) is True
    second.close()


def test_the_name_is_per_user_and_says_nothing_of_who(installed):
    a = server_name(installed, user="alice")
    b = server_name(installed, user="bob")

    assert a != b
    assert a.startswith("Bartleby.Sample.")
    assert "alice" not in a


def test_each_product_has_a_name_of_its_own(installed):
    assert server_name(installed, user="u") != server_name(replace(installed, name="Other"), user="u")


def test_a_checkout_never_shares_a_name_with_the_installed_app(installed, sample_product):
    """Done when (BartleBot #237): a checkout in developer mode never hands its arguments to an installed app."""
    names = {
        server_name(installed, user="u"),
        server_name(sample_product, user="u"),
        server_name(replace(sample_product, dev_mode=True), user="u"),
    }

    assert len(names) == 3


def test_the_name_follows_the_products_developer_mode(sample_product):
    assert server_name(replace(sample_product, dev_mode=True)) == server_name(sample_product) + ".dev"


def test_a_checkout_under_dev_does_not_reach_an_installed_app(qtbot, qapp, installed, sample_product):
    user = f"test-{uuid.uuid4().hex}"
    running = SingleInstance(installed, name=server_name(installed, user=user))
    running.claim([])
    got = []
    running.arguments_received.connect(got.append)

    try:
        launch = _later_launch(server_name(replace(sample_product, dev_mode=True), user=user), "Gift.bbot")
        assert not _handed_over(qtbot, launch)
        assert got == []
    finally:
        running.close()


def test_the_queue_releases_at_once_when_the_app_is_free(qtbot, qapp):
    queue = LaunchQueue(lambda: False)

    with qtbot.waitSignal(queue.ready, timeout=1000) as released:
        queue.put(["a.bbot"])

    assert released.args == [["a.bbot"]]


def test_the_queue_waits_for_a_modal_dialog_to_close(qtbot, qapp, monkeypatch):
    monkeypatch.setattr(single_instance, "BUSY_RETRY_MS", 10)
    busy = [True]
    queue = LaunchQueue(lambda: busy[0])
    got = []
    queue.ready.connect(got.append)

    queue.put(["a.bbot"])
    queue.put(["b.bbot"])
    qtbot.wait(60)
    assert got == []

    busy[0] = False
    qtbot.waitUntil(lambda: bool(got), timeout=1000)
    assert got == [["a.bbot", "b.bbot"]]


def test_raising_restores_a_minimized_window(qapp):
    window = QWidget()
    window.showMinimized()

    single_instance.raise_window(window)

    assert not window.isMinimized()
