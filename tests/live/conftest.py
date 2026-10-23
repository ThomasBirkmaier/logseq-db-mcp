"""Live tests: against a running Logseq with the test graph SpikeTest open.

Run with ``uv run pytest -m live``. They write into SpikeTest (everything is
named ``LT <run> …``) and refuse to run while any other graph is open.
"""

import json
import os
import time
from pathlib import Path

import pytest

from logseq_db_mcp.client import LogseqClient
from logseq_db_mcp.server import dispatch, registry
from logseq_db_mcp.settings import Settings
from logseq_db_mcp.tools.base import Context

TEST_GRAPH = "logseq_db_SpikeTest"
CONFIG = Path.home() / "Library/Application Support/Claude/claude_desktop_config.json"


def _token() -> str:
    if os.getenv("LOGSEQ_API_TOKEN"):
        return os.environ["LOGSEQ_API_TOKEN"]
    servers = json.loads(CONFIG.read_text())["mcpServers"]
    for s in servers.values():
        token = (s.get("env") or {}).get("LOGSEQ_API_TOKEN")
        if token:
            return token
    pytest.skip("no LOGSEQ_API_TOKEN")


def make_settings(**overrides) -> Settings:
    base = dict(api_key=_token(), base_url="http://127.0.0.1:12315", verify_ssl=False,
                connect_timeout=3, read_timeout=30, enable_call_api=True)
    return Settings(**{**base, **overrides})


@pytest.fixture(scope="session")
def settings():
    s = make_settings()
    try:
        graph = LogseqClient(s).call("logseq.App.getCurrentGraph")
    except Exception as e:
        pytest.skip(f"Logseq not reachable: {e}")
    if (graph or {}).get("name") != TEST_GRAPH:
        pytest.exit(f"Live tests write into the graph; open {TEST_GRAPH} (now: {(graph or {}).get('name')})", 2)
    return s


@pytest.fixture(scope="session")
def run_id():
    return time.strftime("%m%d%H%M%S")


class Runner:
    def __init__(self, settings: Settings):
        self.ctx = Context.create(settings)
        self.tools = registry(settings, read_only=False)

    def __call__(self, name: str, **args):
        r = dispatch(self.ctx, self.tools, name, args)
        text = r.content[0].text
        if r.is_error:
            raise AssertionError(f"{name} failed: {text}")
        return json.loads(text)

    def error(self, name: str, **args) -> str:
        r = dispatch(self.ctx, self.tools, name, args)
        assert r.is_error, f"{name} should fail, got {r.content[0].text[:300]}"
        return r.content[0].text


@pytest.fixture(scope="session")
def tool(settings):
    return Runner(settings)


@pytest.fixture(scope="session")
def model(tool, run_id):
    """Test schema: user properties of every type (via upsertNodes) and a tag hierarchy."""
    c = tool.ctx.client
    p = f"LT {run_id}"
    props = {"text": "default", "number": "number", "check": "checkbox", "url": "url", "date": "date",
             "when": "datetime", "ref": "node", "refs": "node", "choice": "default"}
    ops = [{"operation": "add", "entityType": "property", "id": None,
            "data": {"title": f"{p} {k}", "property-type": t,
                     "property-cardinality": "many" if k == "refs" else "one"}}
           for k, t in props.items()]
    c.mcp_tool("upsertNodes", {"operations": ops})
    for color in ("Rot", "Grün"):
        c.call("logseq.Editor.createPage", f"{p} {color}", {}, {"createFirstBlock": False})
    tool.ctx.schema.invalidate()
    ch = tool.ctx.schema.property(f"{p} choice")
    uuids = [tool.ctx.graph.entities_by_title(f"{p} {color}")[0]["uuid"] for color in ("Rot", "Grün")]
    c.call("logseq.Editor.addPropertyValueChoices", ch.uuid, uuids)
    base = c.call("logseq.Editor.createTag", f"{p} Basis",
                  {"tagProperties": [{"name": f"lt{run_id}Pflicht", "schema": {"type": "default"}}]})
    sub = c.call("logseq.Editor.createTag", f"{p} Unter")
    c.call("logseq.Editor.addTagExtends", sub["uuid"], base["uuid"])
    other = c.call("logseq.Editor.createTag", f"{p} Andere")
    target = c.call("logseq.Editor.createPage", f"{p} Ziel", {}, {"createFirstBlock": False})
    c.call("logseq.Editor.createPage", f"{p} Zweitziel", {}, {"createFirstBlock": False})
    tool.ctx.schema.invalidate()
    return {"p": p, "base": base["uuid"], "sub": sub["uuid"], "other": other["uuid"],
            "target": target["uuid"], "rot": f"{p} Rot", "gruen": f"{p} Grün"}
