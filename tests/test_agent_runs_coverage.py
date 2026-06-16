import asyncio

import pytest


@pytest.fixture(autouse=True)
def clear_agent_runs():
    import src.agent_runs as agent_runs

    agent_runs._RUNS.clear()
    yield
    for run in list(agent_runs._RUNS.values()):
        for task in (run.task, run.evict_task):
            if task and not task.done():
                task.cancel()
    agent_runs._RUNS.clear()


@pytest.mark.asyncio
async def test_agent_run_start_subscribe_done_and_evict(monkeypatch):
    import src.agent_runs as agent_runs

    async def gen():
        yield "data: one\n\n"
        await asyncio.sleep(0)
        yield "data: two\n\n"

    run = agent_runs.start("s1", gen())
    assert agent_runs.is_active("s1") is True
    assert agent_runs.get_status("s1") == "running"

    events = []
    async for event in agent_runs.subscribe("s1"):
        events.append(event)

    await run.task
    assert events == ["data: one\n\n", "data: two\n\n"]
    assert agent_runs.get_status("s1") == "done"
    assert agent_runs.stop("s1") is False

    monkeypatch.setattr(agent_runs, "_EVICT_GRACE_S", 0)
    agent_runs._schedule_evict("s1")
    await run.evict_task
    assert agent_runs.get_status("s1") is None
    assert agent_runs.is_active("s1") is False


@pytest.mark.asyncio
async def test_agent_run_error_publishes_error_and_done_events():
    import src.agent_runs as agent_runs

    async def broken():
        yield "data: before\n\n"
        raise RuntimeError("boom")

    run = agent_runs.start("err", broken())
    await run.task

    assert agent_runs.get_status("err") == "error"
    assert run.buffer[0] == "data: before\n\n"
    assert "event: error" in run.buffer[1]
    assert '"status": 500' in run.buffer[1]
    assert run.buffer[2] == "data: [DONE]\n\n"

    replay = []
    async for event in agent_runs.subscribe("err"):
        replay.append(event)
    assert replay == run.buffer


@pytest.mark.asyncio
async def test_agent_run_stop_closes_generator_and_wakes_subscriber():
    import src.agent_runs as agent_runs

    closed = False

    async def slow():
        nonlocal closed
        try:
            yield "data: start\n\n"
            await asyncio.Event().wait()
        finally:
            closed = True

    run = agent_runs.start("slow", slow())
    first = []

    async def collect():
        async for event in agent_runs.subscribe("slow"):
            first.append(event)

    subscriber = asyncio.create_task(collect())
    await asyncio.sleep(0)
    assert first == ["data: start\n\n"]

    assert agent_runs.stop("slow") is True
    await run.task
    await subscriber

    assert closed is True
    assert agent_runs.get_status("slow") == "stopped"


@pytest.mark.asyncio
async def test_agent_run_replaces_previous_and_ignores_missing_subscribe():
    import src.agent_runs as agent_runs

    assert [event async for event in agent_runs.subscribe("missing")] == []
    assert agent_runs.stop("missing") is False

    release_old = asyncio.Event()
    release_new = asyncio.Event()

    async def old_gen():
        yield "old\n"
        await release_old.wait()

    async def new_gen():
        yield "new\n"
        release_new.set()

    old = agent_runs.start("same", old_gen())
    await asyncio.sleep(0)
    new = agent_runs.start("same", new_gen())
    await release_new.wait()
    release_old.set()
    await new.task

    assert old.task.cancelled() or old.status in {"stopped", "running"}
    assert agent_runs.get_status("same") == "done"
    replay = [event async for event in agent_runs.subscribe("same")]
    assert replay == ["new\n"]


@pytest.mark.asyncio
async def test_agent_run_defensive_paths_and_raced_sentinel(monkeypatch):
    import src.agent_runs as agent_runs

    class BadQueue:
        def put_nowait(self, _item):
            raise RuntimeError("closed")

    run = agent_runs._Run()
    run.subscribers.add(BadQueue())
    agent_runs._publish(run, "data: ignored\n\n")
    assert run.buffer == ["data: ignored\n\n"]
    agent_runs._schedule_evict("missing")

    async def empty_gen():
        if False:
            yield "never"

    await agent_runs._drain("missing", empty_gen())

    run_wait = agent_runs._Run()
    agent_runs._RUNS["waiterr"] = run_wait
    prev_task = asyncio.create_task(asyncio.Event().wait())
    real_wait = agent_runs.asyncio.wait

    async def wait_raises(_tasks):
        raise RuntimeError("wait failed")

    monkeypatch.setattr(agent_runs.asyncio, "wait", wait_raises)

    async def one_event():
        yield "data: after wait\n\n"

    await agent_runs._drain("waiterr", one_event(), prev_task)
    prev_task.cancel()
    assert run_wait.buffer == ["data: after wait\n\n"]
    monkeypatch.setattr(agent_runs.asyncio, "wait", real_wait)

    run_wait_cancel = agent_runs._Run()
    agent_runs._RUNS["wait-cancel"] = run_wait_cancel
    waiting_prev = asyncio.create_task(asyncio.Event().wait())
    drain_task = asyncio.create_task(agent_runs._drain("wait-cancel", empty_gen(), waiting_prev))
    await asyncio.sleep(0)
    drain_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await drain_task
    waiting_prev.cancel()

    class BlockingAgen:
        def __init__(self):
            self.ready = asyncio.Event()

        def __aiter__(self):
            return self

        async def __anext__(self):
            self.ready.set()
            await asyncio.Event().wait()

        async def aclose(self):
            raise RuntimeError("close failed")

    run_cancel = agent_runs._Run()
    run_cancel.subscribers.add(BadQueue())
    agent_runs._RUNS["cancel"] = run_cancel
    agen = BlockingAgen()
    task = asyncio.create_task(agent_runs._drain("cancel", agen))
    await agen.ready.wait()
    task.cancel()
    await task
    assert run_cancel.status == "stopped"

    async def done_gen():
        yield "done\n"

    async def sleeper():
        await asyncio.sleep(10)

    old_run = agent_runs._Run()
    old_run.evict_task = asyncio.create_task(sleeper())
    agent_runs._RUNS["replace-evict"] = old_run
    replacement = agent_runs.start("replace-evict", done_gen())
    await replacement.task
    await asyncio.sleep(0)
    assert old_run.evict_task.cancelled()

    raced = agent_runs._Run()
    raced.buffer.append("first\n")
    agent_runs._RUNS["raced"] = raced
    collected = []

    async def collect():
        async for event in agent_runs.subscribe("raced"):
            collected.append(event)

    subscriber = asyncio.create_task(collect())
    await asyncio.sleep(0)
    queue = next(iter(raced.subscribers))
    raced.buffer.append("tail\n")
    queue.put_nowait((None, None))
    await subscriber
    assert collected == ["first\n", "tail\n"]
