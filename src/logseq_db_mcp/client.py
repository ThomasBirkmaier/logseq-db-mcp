"""HTTP client for Logseq: the plugin API at /api and Logseq's own MCP server at /mcp.

Every call is a single request; nothing is retried. A write that timed out may
still have happened, so retrying could write twice (see docs/API-BEFUNDE.md).
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Callable

import requests

from .settings import Settings

logger = logging.getLogger("logseq-db-mcp")

UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


class LogseqError(RuntimeError):
    """Logseq rejected a call or could not be reached."""


def edn_uuid(uuid: str) -> str:
    """``#uuid "…"`` for a Datascript input; refuses anything but a UUID."""
    if not isinstance(uuid, str) or not UUID_RE.match(uuid):
        raise ValueError(f"not a UUID: {uuid!r}")
    return f'#uuid "{uuid}"'


def edn_uuids(uuids) -> str:
    return "[" + " ".join(edn_uuid(u) for u in uuids) + "]"


def edn_int(i) -> str:
    return str(int(i))


def edn_ints(ids) -> str:
    return "[" + " ".join(str(int(i)) for i in ids) + "]"


def edn_str(s: str) -> str:
    return json.dumps(s, ensure_ascii=False)


Recorder = Callable[[str, str, list, Any], None]


class LogseqClient:
    def __init__(self, settings: Settings, recorder: Recorder | None = None):
        self.settings = settings
        self.recorder = recorder
        self._mcp_session: str | None = None
        self._mcp_id = 0

    # --- plugin API ---------------------------------------------------------

    def _post(self, path: str, payload: dict, headers: dict | None = None) -> requests.Response:
        return requests.post(
            self.settings.base_url + path,
            headers={"Authorization": f"Bearer {self.settings.api_key}", **(headers or {})},
            json=payload,
            verify=self.settings.verify_ssl,
            timeout=self.settings.timeout,
        )

    def call(self, method: str, *args) -> Any:
        """One plugin-API call. Raises ``LogseqError`` with Logseq's reason."""
        try:
            r = self._post("/api", {"method": method, "args": list(args)})
        except requests.RequestException as e:
            raise LogseqError(f"{method}: Logseq not reachable ({type(e).__name__}: {e})") from e
        try:
            body = r.json()
        except ValueError:
            body = r.text
        if self.recorder:
            self.recorder("api", method, list(args), body)
        if r.status_code != 200:
            detail = body.get("error") if isinstance(body, dict) else str(body)[:500]
            raise LogseqError(f"{method}: HTTP {r.status_code}: {detail}")
        if isinstance(body, dict) and set(body) == {"error"}:
            raise LogseqError(f"{method}: {body['error']}")
        return body

    def q(self, query: str, *inputs: str) -> list:
        """Datascript query; inputs are EDN strings (``edn_uuid`` etc.)."""
        result = self.call("logseq.DB.datascriptQuery", query, *inputs)
        return result if isinstance(result, list) else []

    # --- Logseq's own MCP server (only for upsertNodes) ---------------------

    def _mcp_post(self, message: dict) -> Any:
        headers = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
        if self._mcp_session:
            headers["Mcp-Session-Id"] = self._mcp_session
        try:
            r = self._post("/mcp", message, headers)
        except requests.RequestException as e:
            raise LogseqError(f"Logseq MCP not reachable ({type(e).__name__}: {e})") from e
        if "mcp-session-id" in r.headers:
            self._mcp_session = r.headers["mcp-session-id"]
        text = r.text
        if "data:" in text:
            text = [line[5:] for line in text.splitlines() if line.startswith("data:")][-1]
        return json.loads(text) if text.strip() else None

    def mcp_tool(self, tool: str, arguments: dict) -> str:
        """Call a tool of Logseq's MCP server. Raises on failure.

        Failures come back as text starting with "API Error" without the
        protocol's error flag, so the text is checked as well.
        """
        if self._mcp_session is None:
            self._mcp_post({"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {
                "protocolVersion": "2025-06-18", "capabilities": {},
                "clientInfo": {"name": "logseq-db-mcp", "version": "0"}}})
            self._mcp_post({"jsonrpc": "2.0", "method": "notifications/initialized"})
        self._mcp_id += 1
        body = self._mcp_post({"jsonrpc": "2.0", "id": self._mcp_id, "method": "tools/call",
                               "params": {"name": tool, "arguments": arguments}}) or {}
        if self.recorder:
            self.recorder("mcp", tool, [arguments], body)
        if "error" in body:
            raise LogseqError(f"{tool}: {body['error'].get('message', body['error'])}")
        result = body.get("result", {})
        text = (result.get("content") or [{}])[0].get("text", "")
        if result.get("isError") or text.startswith("API Error"):
            raise LogseqError(f"{tool}: {text}")
        return text
