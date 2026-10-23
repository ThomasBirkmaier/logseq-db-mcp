"""Registry, dispatch and the MCP protocol surface, without Logseq."""

import json

import pytest

from logseq_db_mcp.client import LogseqError
from logseq_db_mcp.server import build_app, dispatch, registry
from logseq_db_mcp.tools import all_tools
from logseq_db_mcp.tools.base import Context

from .conftest import FakeClient, settings


def test_registry():
    names = set(registry(settings(), read_only=False))
    assert {"get_schema", "find_objects", "create_objects", "move_blocks", "delete_nodes"} <= names
    assert "call_api" not in names  # opt-in
    assert "call_api" in registry(settings(enable_call_api=True), read_only=False)
    ro = registry(settings(enable_call_api=True), read_only=True)
    assert all(not t.writes for t in ro.values()) and "call_api" not in ro


def test_every_tool_describes_itself():
    for t in all_tools():
        d = t.describe()
        assert d.name and d.description and d.input_schema["type"] == "object"
        assert d.annotations.read_only_hint is (not t.writes)


def _ctx(respond=None, **s):
    st = settings(**s)
    return Context.create(st, FakeClient(respond, st)), registry(st, read_only=False)


def test_dispatch_errors_are_in_band():
    ctx, tools = _ctx()
    assert dispatch(ctx, tools, "nope", {}).is_error
    r = dispatch(ctx, tools, "get_node", {"uuid": "not-a-uuid"})
    assert r.is_error and "Invalid arguments" in r.content[0].text

    def fail(method, args):
        raise LogseqError("Logseq not reachable")
    ctx, tools = _ctx(fail)
    r = dispatch(ctx, tools, "get_node", {"uuid": "11111111-1111-4111-8111-111111111111"})
    assert r.is_error and "not reachable" in r.content[0].text


def test_unknown_uuid_is_reported():
    ctx, tools = _ctx(lambda m, a: [])
    r = dispatch(ctx, tools, "get_node", {"uuid": "11111111-1111-4111-8111-111111111111"})
    assert r.is_error and "no node with UUID" in r.content[0].text


def test_call_api_denied_with_rules():
    ctx, tools = _ctx(lambda m, a: {"name": "x"}, enable_call_api=True, write_pages=("A",))
    r = dispatch(ctx, tools, "call_api", {"method": "logseq.App.getCurrentGraph"})
    assert r.is_error and "access rules" in r.content[0].text
    ctx, tools = _ctx(lambda m, a: {"name": "x"}, enable_call_api=True)
    r = dispatch(ctx, tools, "call_api", {"method": "logseq.App.getCurrentGraph"})
    assert json.loads(r.content[0].text)["result"] == {"name": "x"}


def test_query_refused_with_hide_tags():
    ctx, tools = _ctx(lambda m, a: [], hide_tags=("Privat",))
    r = dispatch(ctx, tools, "query", {"query": "[:find ?e :where [?e :block/uuid]]"})
    assert r.is_error and "LOGSEQ_HIDE_TAGS" in r.content[0].text


def test_write_audit_line(caplog):
    ctx, tools = _ctx(lambda m, a: {"ok": True}, enable_call_api=True)
    with caplog.at_level("INFO", logger="logseq-db-mcp"):
        dispatch(ctx, tools, "call_api", {"method": "logseq.Editor.getBlock", "args": ["x"]})
    assert "Write: tool=call_api nodes=(api logseq.Editor.getBlock)" in caplog.text


@pytest.mark.asyncio
async def test_mcp_protocol_lists_and_calls_tools():
    """A real ClientSession talks to the real Server over in-memory streams (pattern from mcp-logseq)."""
    from functools import partial

    import anyio
    from mcp.client.session import ClientSession
    from mcp.shared.memory import create_client_server_memory_streams

    st = settings()
    app, tools = build_app(st, read_only=True, ctx=Context.create(st, FakeClient(lambda m, a: [], st)))
    async with create_client_server_memory_streams() as ((c_read, c_write), (s_read, s_write)):
        async with anyio.create_task_group() as tg:
            tg.start_soon(partial(app.run, s_read, s_write, app.create_initialization_options(),
                                  raise_exceptions=True))
            async with ClientSession(c_read, c_write) as session:
                await session.initialize()
                listed = await session.list_tools()
                missing = await session.call_tool("get_node", {"uuid": "11111111-1111-4111-8111-111111111111"})
            tg.cancel_scope.cancel()
    assert [t.name for t in listed.tools] == list(tools)
    assert missing.is_error and "no node with UUID" in missing.content[0].text
