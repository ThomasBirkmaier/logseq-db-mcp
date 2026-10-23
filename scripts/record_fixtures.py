"""Record real Logseq answers for the offline snapshot tests (tests/unit/test_snapshots.py).

Needs SpikeTest open and a live-test run before (it reads the newest "LT …"
objects). Only reads. Writes tests/fixtures/spiketest.json: every API call
with its answer, plus the tool outputs built from them.

    uv run --no-editable python scripts/record_fixtures.py
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tests.live.conftest import TEST_GRAPH, make_settings  # noqa: E402

from logseq_db_mcp.client import LogseqClient  # noqa: E402
from logseq_db_mcp.server import dispatch, registry  # noqa: E402
from logseq_db_mcp.tools.base import Context  # noqa: E402

calls: list = []


def recorder(kind, name, args, result):
    calls.append({"kind": kind, "name": name, "args": args, "result": result})


settings = make_settings(enable_call_api=False)
client = LogseqClient(settings, recorder=recorder)
if client.call("logseq.App.getCurrentGraph")["name"] != TEST_GRAPH:
    sys.exit(f"open {TEST_GRAPH} first")
calls.clear()
ctx = Context.create(settings, client)
tools = registry(settings, read_only=True)


def run(name, **args):
    r = dispatch(ctx, tools, name, args)
    assert not r.is_error, r.content[0].text
    return json.loads(r.content[0].text)


prefixes = sorted({t["title"].rsplit(" ", 1)[0] for t in run("get_schema")["tags"] if t["title"].startswith("LT ")})
if not prefixes:
    sys.exit("no 'LT …' test data; run the live tests first")
p = prefixes[-1]
calls.clear()
ctx.schema.invalidate()
cases = []
page = run("resolve", title=f"{p} Seite")["matches"][0]["uuid"]
for name, args in [
    ("get_schema", {}),
    ("get_node", {"uuid": page, "depth": 2}),
    ("find_objects", {"tag": f"{p} Basis"}),
    ("find_objects", {"tag": f"{p} Basis", "where": [{"property": f"{p} number", "op": "gt", "value": 3}]}),
    ("get_backlinks", {"uuid": run("resolve", title=f"{p} Ziel")["matches"][0]["uuid"]}),
]:
    calls_before = len(calls)
    ctx.schema.invalidate()
    out = run(name, **args)
    cases.append({"tool": name, "args": args, "output": out, "calls": calls[calls_before:]})

target = ROOT / "tests/fixtures/spiketest.json"
target.write_text(json.dumps({"prefix": p, "cases": cases}, ensure_ascii=False, separators=(",", ":")))
print(f"{target}: {len(cases)} cases, {sum(len(c['calls']) for c in cases)} calls")
