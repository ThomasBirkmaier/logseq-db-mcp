"""MCP server: tool registry and dispatch."""

from __future__ import annotations

import asyncio
import json
import logging
from importlib.metadata import PackageNotFoundError, version as pkg_version

import jsonschema
from mcp.server import Server, ServerRequestContext
from mcp.types import (
    CallToolRequestParams,
    CallToolResult,
    ListToolsResult,
    PaginatedRequestParams,
    TextContent,
)

from .access import AccessDenied
from .client import LogseqError
from .schema import SchemaError
from .settings import Settings
from .tools import all_tools
from .tools.base import Context, Tool
from .values import InvalidValue

logger = logging.getLogger("logseq-db-mcp")

MAX_OUTPUT = 100_000

# Expected failures: their message is the whole story, no traceback needed.
EXPECTED = (AccessDenied, LogseqError, SchemaError, InvalidValue, LookupError, ValueError, PermissionError)


def _error(message: str) -> CallToolResult:
    return CallToolResult(content=[TextContent(type="text", text=message)], is_error=True)


def registry(settings: Settings, read_only: bool) -> dict[str, Tool]:
    tools = {}
    for t in all_tools():
        if read_only and t.writes:
            continue
        if t.name == "call_api" and not settings.enable_call_api:
            continue
        tools[t.name] = t
    return tools


def dispatch(ctx: Context, tools: dict[str, Tool], name: str, arguments: dict) -> CallToolResult:
    """Validate, run, serialize. Argument and result bodies are never logged."""
    logger.info(f"Tool call: {name} (argument keys: {', '.join(sorted(arguments)) or 'none'})")
    tool = tools.get(name)
    if tool is None:
        return _error(f"Unknown tool: {name}")
    try:
        jsonschema.validate(arguments, tool.input_schema)
    except jsonschema.ValidationError as e:
        return _error(f"Invalid arguments: {e.message}")
    try:
        result = tool.run(ctx, arguments)
    except EXPECTED as e:
        logger.warning(f"{name} failed: {e}")
        return _error(str(e))
    except Exception as e:
        logger.error(f"{name} crashed", exc_info=True)
        return _error(f"Internal error in {name}: {type(e).__name__}: {e}")
    if tool.writes and isinstance(result, dict) and result.get("changed"):
        logger.info(f"Write: tool={name} nodes={', '.join(map(str, result['changed']))}")
    text = json.dumps(result, ensure_ascii=False, default=str, separators=(",", ":"))
    if len(text) > MAX_OUTPUT:
        text = text[:MAX_OUTPUT] + f"… [truncated: {len(text)} characters; narrow the request]"
    return CallToolResult(content=[TextContent(type="text", text=text)])


def build_app(settings: Settings, read_only: bool = False, ctx: Context | None = None) -> tuple[Server, dict]:
    ctx = ctx or Context.create(settings)
    tools = registry(settings, read_only)

    async def list_tools(rc: ServerRequestContext, params: PaginatedRequestParams | None) -> ListToolsResult:
        return ListToolsResult(tools=[t.describe() for t in tools.values()])

    async def call_tool(rc: ServerRequestContext, params: CallToolRequestParams) -> CallToolResult:
        # One call at a time: Logseq's API has no transactions, and the
        # schema cache is not thread-safe.
        async with lock:
            return await asyncio.to_thread(dispatch, ctx, tools, params.name, params.arguments or {})

    lock = asyncio.Lock()
    try:
        ver = pkg_version("logseq-db-mcp")
    except PackageNotFoundError:
        ver = ""
    return Server("logseq-db-mcp", version=ver, on_list_tools=list_tools, on_call_tool=call_tool), tools


async def serve_stdio(settings: Settings, read_only: bool) -> None:
    from mcp.server.stdio import stdio_server

    app, tools = build_app(settings, read_only)
    logger.info(f"logseq-db-mcp on {settings.base_url}, {len(tools)} tools (read_only={read_only})")
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())
