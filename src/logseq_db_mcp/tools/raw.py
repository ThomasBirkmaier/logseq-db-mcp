"""Raw plugin-API passthrough (``call_api``), adapted from mcp-logseq (see NOTICE).

An escape hatch for what no dedicated tool covers. Opt-in via
``LOGSEQ_ENABLE_CALL_API``; denied while any access rule is set.
"""

from __future__ import annotations

import json
import re
from typing import Any

from .base import Context, Tool

# logseq.Git is left out on purpose: Git.execCommand runs arbitrary git commands.
METHOD_RE = re.compile(r"^logseq\.(Editor|DB|App|UI|Assets)\.[A-Za-z][A-Za-z0-9]*$")

# Methods that act on the app, the OS or the network instead of the graph.
BLOCKED_METHODS = frozenset({
    "logseq.App.quit",
    "logseq.App.relaunch",
    "logseq.App.execGitCommand",
    "logseq.App.openExternalLink",
    "logseq.App.openPath",
})

DEFAULT_MAX_CHARS = 20000


def validate_method(method: str) -> None:
    if not METHOD_RE.match(method):
        raise ValueError(f"Invalid method '{method}': expected logseq.<Editor|DB|App|UI|Assets>.<name>")
    if method in BLOCKED_METHODS:
        raise ValueError(f"Method '{method}' is blocked for call_api")


class CallApi(Tool):
    name = "call_api"
    writes = True
    destructive = True
    description = (
        "Call any Logseq plugin API method and return the raw JSON result. Only when no dedicated "
        "tool fits. Arguments are positional, as in the plugin API. Calls can modify the graph, and "
        "Logseq often answers null even when nothing happened: read the result back.")
    input_schema = {"type": "object", "properties": {
        "method": {"type": "string", "pattern": METHOD_RE.pattern,
                   "description": "e.g. 'logseq.Editor.getBlock'"},
        "args": {"type": "array", "default": []},
        "max_chars": {"type": "integer", "minimum": 100, "default": DEFAULT_MAX_CHARS}},
        "required": ["method"]}

    def run(self, ctx: Context, args: dict) -> Any:
        ctx.access.check_raw()
        method = args["method"]
        validate_method(method)
        call_args = args.get("args", [])
        if not isinstance(call_args, list):
            raise ValueError("args must be an array")
        result = ctx.client.call(method, *call_args)
        text = json.dumps(result, ensure_ascii=False, default=str)
        max_chars = args.get("max_chars", DEFAULT_MAX_CHARS)
        if len(text) > max_chars:
            return {"truncated": True, "length": len(text), "json": text[:max_chars]}
        return {"result": result, "changed": [f"(api {method})"]}
