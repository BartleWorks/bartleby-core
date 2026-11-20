"""The run trace: spans, the hooks on wizwalker, and the file a run leaves behind."""

import asyncio
import json
import threading
import time
from types import SimpleNamespace

import pytest
from wizwalker.client import Client
from wizwalker.memory.handler import HookHandler
from wizwalker.memory.memory_objects.window import Window
from wizwalker.memory.memory_reader import MemoryReader
from wizwalker.mouse_handler import MouseHandler

from bartleby.runtime import run_trace


@pytest.fixture
def dev(monkeypatch):
    monkeypatch.setenv("BARTLEBOT_DEV", "1")


@pytest.fixture
def stopped():
    """Never leave a trace, or its hooks on wizwalker's classes, to the next test."""
    yield
    run_trace.stop()


def _records(path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _spans(records) -> dict[str, dict]:
    return {r["name"]: r for r in records if r["kind"] == "span"}


class _FakeProcess:
    def read_bytes(self, address, size):
        return b"\0" * size


def _reader() -> MemoryReader:
    reader = object.__new__(MemoryReader)
    reader.process = _FakeProcess()
    return reader


async def test_spans_nest_and_a_task_started_inside_one_belongs_to_it(dev, tmp_path, stopped):
    clients = [object(), object()]
    path = run_trace.start(lambda c: clients.index(c) + 1, tmp_path)

    async def per_client(client):
        with run_trace.span("inner", client=client):
            with run_trace.span(f"leaf{clients.index(client)}"):
                await asyncio.sleep(0)

    with run_trace.span("outer"):
        await asyncio.gather(*(per_client(c) for c in clients))
    run_trace.stop()

    records = _records(path)
    assert records[0]["kind"] == "start"
    spans = _spans(records)
    inner = [r for r in records if r.get("name") == "inner"]
    assert {r["parent"] for r in inner} == {spans["outer"]["id"]}
    assert {r["slot"] for r in inner} == {1, 2}
    # A span with no player of its own takes its parent's.
    assert spans["leaf0"]["slot"] == 1 and spans["leaf1"]["slot"] == 2
    assert spans["outer"]["slot"] is None


async def test_the_innermost_span_naming_an_attribute_answers_it_and_its_player(dev, tmp_path, stopped):
    """What a task handed work from another span reads of it: heap_scan's window and slot."""
    run_trace.start(lambda c: None, tmp_path)
    assert (run_trace.enclosing("hwnd"), run_trace.current_slot()) == (None, None)
    with run_trace.span("find", slot=2, hwnd=77), run_trace.span("nodes"):
        assert (run_trace.enclosing("hwnd"), run_trace.current_slot()) == (77, 2)
        with run_trace.span("closer", hwnd=88):
            assert run_trace.enclosing("hwnd") == 88
    assert run_trace.enclosing("hwnd") is None


async def test_reads_count_against_the_innermost_span(dev, tmp_path, stopped):
    path = run_trace.start(lambda c: None, tmp_path)
    reader = _reader()
    with run_trace.span("outer"):
        await reader.read_bytes(0x1000, 8)
        with run_trace.span("inner"):
            for _ in range(3):
                await reader.read_bytes(0x1000, 8)
    run_trace.stop()

    spans = _spans(_records(path))
    assert spans["outer"]["reads"] == 1
    assert spans["inner"]["reads"] == 3


def _hooked() -> tuple:
    return (
        MemoryReader.read_bytes,
        MemoryReader.__dict__["run_in_executor"],
        HookHandler._wait_for_value,
        Client.send_key,
        MouseHandler.click,
        Window.get_windows_with_name,
    )


async def test_stopping_puts_wizwalkers_own_methods_back(dev, tmp_path, stopped):
    originals = _hooked()
    run_trace.start(lambda c: None, tmp_path)
    assert all(now is not was for now, was in zip(_hooked(), originals, strict=True))
    run_trace.stop()
    assert _hooked() == originals


async def test_a_mark_from_another_thread_is_written(dev, tmp_path, stopped):
    path = run_trace.start(lambda c: 4, tmp_path)
    client = object()
    run_trace.mark("on the loop", client=client, hwnd=7)
    thread = threading.Thread(target=lambda: run_trace.mark("on the gui", slot=2, members=3))
    thread.start()
    thread.join()
    run_trace.stop()

    marks = {r["name"]: r for r in _records(path) if r["kind"] == "mark"}
    assert (marks["on the loop"]["slot"], marks["on the loop"]["hwnd"]) == (4, 7)
    assert (marks["on the gui"]["slot"], marks["on the gui"]["members"]) == (2, 3)


async def test_a_mark_with_no_trace_running_does_nothing(stopped):
    run_trace.mark("nobody listening", hwnd=1)


async def test_an_executor_job_is_split_into_its_wait_and_its_run(dev, tmp_path, stopped):
    def _scan_all():
        time.sleep(0.05)
        return [1]

    # A function of this library's, as client_resizer's _nodes_called is.
    namespace = {"__name__": "bartleby.runtime.sample", "MemoryReader": MemoryReader, "_scan_all": _scan_all}
    exec("async def find():\n    return await MemoryReader.run_in_executor(_scan_all)\n", namespace)

    path = run_trace.start(lambda c: None, tmp_path)
    found = await namespace["find"]()
    run_trace.stop()

    (job,) = [r for r in _records(path) if r["kind"] == "executor"]
    assert found == [1]
    assert job["fn"] == "_scan_all"
    assert job["caller"] == "runtime.sample.find:2"
    assert job["run"] >= 0.04
    assert job["wait"] < job["run"]
    assert job["dur"] >= job["run"]


async def test_an_executor_job_that_raises_is_still_written(dev, tmp_path, stopped):
    def fails():
        raise ValueError("no region")

    path = run_trace.start(lambda c: None, tmp_path)
    with pytest.raises(ValueError):
        await MemoryReader.run_in_executor(fails)
    run_trace.stop()

    assert [r["fn"] for r in _records(path) if r["kind"] == "executor"] == ["fails"]


async def test_a_wait_for_a_hook_address_is_a_span_named_by_that_address(dev, tmp_path, stopped, monkeypatch):
    client = object()
    handler = object.__new__(HookHandler)
    handler.client = client
    handler._base_addrs = {"player_struct": 0x10, "current_client": 0x20}

    async def read_typed(self, address, kind):
        return 0x1234  # written already, so the wait ends at its first read

    monkeypatch.setattr(HookHandler, "read_typed", read_typed)
    path = run_trace.start(lambda c: 2 if c is client else None, tmp_path)
    await handler._wait_for_value(0x10)
    run_trace.stop()

    wait = _spans(_records(path))["hook.wait"]
    assert wait["attrs"] == {"what": "player_struct"}
    assert wait["slot"] == 2


async def test_a_key_names_the_bartlebot_function_that_pressed_it(dev, tmp_path, stopped, monkeypatch):
    sent = []

    async def timed_send_key(hwnd, key, seconds):
        sent.append(key)

    monkeypatch.setattr("wizwalker.client.utils.timed_send_key", timed_send_key)
    client = object.__new__(Client)
    client.window_handle = 1
    key = SimpleNamespace(name="P")
    # A function whose module is in bartlebot, as deck_io's deck_page_open is.
    namespace = {"__name__": "sample.combat.example", "key": key}
    exec("async def open_page(client):\n    await client.send_key(key)\n", namespace)

    path = run_trace.start(lambda c: 3 if c is client else None, tmp_path)
    with run_trace.span("deck.match_check"):
        await namespace["open_page"](client)
    run_trace.stop()

    records = _records(path)
    (press,) = [r for r in records if r["kind"] == "key"]
    assert sent == [key]
    assert press["key"] == "P"
    assert press["slot"] == 3
    assert press["caller"] == "combat.example.open_page:2"
    assert press["span"] == _spans(records)["deck.match_check"]["id"]


async def test_through_an_extension_both_ends_of_its_part_are_named(dev, tmp_path, stopped, monkeypatch):
    async def timed_send_key(hwnd, key, seconds):
        pass

    monkeypatch.setattr("wizwalker.client.utils.timed_send_key", timed_send_key)
    client = object.__new__(Client)
    client.window_handle = 1
    # bartlebot -> an extension's entry point -> the extension function that presses.
    inner = {"__name__": "wizwalker.extensions.wizsprinter.inner"}
    exec("async def press(client):\n    await client.send_key('P')\n", inner)
    outer = {"__name__": "wizwalker.extensions.wizsprinter.outer", "press": inner["press"]}
    exec("async def handle_round(client):\n    await press(client)\n", outer)
    ours = {"__name__": "sample.nodes.example", "handle_round": outer["handle_round"]}
    exec("async def fight(client):\n    await handle_round(client)\n", ours)

    path = run_trace.start(lambda c: 1, tmp_path)
    await ours["fight"](client)
    run_trace.stop()

    (press,) = [r for r in _records(path) if r["kind"] == "key"]
    assert press["caller"] == "nodes.example.fight:2"
    assert press["via"] == "wizsprinter.outer.handle_round:2 > wizsprinter.inner.press:2"


async def test_a_click_on_a_window_is_one_click_named_by_the_window(dev, tmp_path, stopped, monkeypatch):
    clicks = []

    async def click(self, x, y, **kwargs):
        clicks.append((x, y))

    monkeypatch.setattr(MouseHandler, "click", click)
    window = SimpleNamespace(
        scale_to_client=lambda: _resolved(SimpleNamespace(center=lambda: (10, 20))),
        name=lambda: _resolved("btnInvite"),
    )
    handler = object.__new__(MouseHandler)
    handler.client = object()

    path = run_trace.start(lambda c: 2, tmp_path)
    await handler.click_window(window)
    await handler.click(5, 6)
    run_trace.stop()

    events = [r for r in _records(path) if r["kind"] == "click"]
    assert clicks == [(10, 20), (5, 6)]
    assert [(e.get("window"), e.get("at")) for e in events] == [("btnInvite", None), (None, [5, 6])]


async def _resolved(value):
    return value


class _FakeWindow(Window):
    def __init__(self, name: str, children=()):
        self._name = name
        self._children = list(children)
        self.hook_handler = SimpleNamespace(client=None)

    async def name(self):
        return self._name

    async def children(self):
        return self._children


async def test_a_search_of_the_ui_tree_is_one_walk(dev, tmp_path, stopped):
    root = _FakeWindow("root", [_FakeWindow("a", [_FakeWindow("Hand")]), _FakeWindow("Hand")])
    path = run_trace.start(lambda c: None, tmp_path)
    found = await root.get_windows_with_name("Hand")
    run_trace.stop()

    walks = [r for r in _records(path) if r["kind"] == "walk"]
    assert len(found) == 2
    assert [(w["name"], w["found"]) for w in walks] == [("Hand", 2)]


async def test_a_blocked_event_loop_is_a_stall(dev, tmp_path, stopped):
    path = run_trace.start(lambda c: None, tmp_path)
    await asyncio.sleep(run_trace.HEARTBEAT_INTERVAL * 2)
    time.sleep(run_trace.STALL_THRESHOLD * 3)  # the stall: nothing else runs
    await asyncio.sleep(run_trace.HEARTBEAT_INTERVAL * 2)
    run_trace.stop()

    stalls = [r for r in _records(path) if r["kind"] == "stall"]
    assert any(s["dur"] >= run_trace.STALL_THRESHOLD * 2 for s in stalls)


async def test_a_span_open_when_the_trace_stops_is_not_written(dev, tmp_path, stopped):
    path = run_trace.start(lambda c: None, tmp_path)
    with run_trace.span("cut short"):
        run_trace.stop()
    assert "cut short" not in _spans(_records(path))


async def test_old_traces_are_pruned(dev, tmp_path, stopped):
    for i in range(run_trace.KEEP_TRACES + 3):
        (tmp_path / f"run_old_{i:02}.jsonl").write_text("", encoding="utf-8")
    run_trace.start(lambda c: None, tmp_path)
    run_trace.stop()
    assert len(list(tmp_path.glob("run_*.jsonl"))) == run_trace.KEEP_TRACES
