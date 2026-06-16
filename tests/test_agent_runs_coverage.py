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
