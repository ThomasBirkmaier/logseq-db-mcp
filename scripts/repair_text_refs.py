"""Repair reference values stored as text (docs/API-BEFUNDE.md, I10).

Finds every value of a reference property that is a text value block holding
a UUID, and rewrites it through update_objects as a real reference. Without
--apply it only prints the dry run.

    uv run --no-editable python scripts/repair_text_refs.py --graph ReqSeq            # dry run
    uv run --no-editable python scripts/repair_text_refs.py --graph ReqSeq --apply    # write
"""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from logseq_db_mcp import values  # noqa: E402
from logseq_db_mcp.server import dispatch, registry  # noqa: E402
from logseq_db_mcp.settings import Settings  # noqa: E402
from logseq_db_mcp.tools.base import Context  # noqa: E402

CONFIG = Path.home() / "Library/Application Support/Claude/claude_desktop_config.json"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--graph", required=True, help="name of the graph that must be open, e.g. ReqSeq")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    servers = json.loads(CONFIG.read_text())["mcpServers"]
    token = next(s["env"]["LOGSEQ_API_TOKEN"] for s in servers.values() if "LOGSEQ_API_TOKEN" in s.get("env", {}))
    s = Settings(api_key=token, base_url="http://127.0.0.1:12315", verify_ssl=False, connect_timeout=3,
                 read_timeout=60)
    ctx = Context.create(s)
    open_graph = ctx.client.call("logseq.App.getCurrentGraph")["name"]
    if open_graph != f"logseq_db_{a.graph}":
        sys.exit(f"open graph is {open_graph}, expected logseq_db_{a.graph}")
    tools = registry(s, read_only=False)
    schema = ctx.schema.get()

    updates, rows = [], []
    for p in schema.properties.values():
        if p.builtin or p.type not in ("node", "page", "class", "entity"):
            continue
        holders = [h for (h,) in ctx.client.q(f"[:find (pull ?b [*]) :where [?b {p.ident} _]]")]
        blocks = ctx.graph.pull_ids(i for h in holders for i in values.referenced_ids(h.get(p.ident)))
        for h in holders:
            ids = values.referenced_ids(h.get(p.ident))
            text = [values.text_ref_uuid(p, blocks.get(i, {})) for i in ids]
            if not any(text):
                continue
            # keep proper references as they are; replace text ones by their target
            targets = []
            for i, t in zip(ids, text):
                targets.append(t if t else (blocks.get(i) or ctx.graph.pull_ids([i]).get(i, {})).get("uuid"))
            value = targets if p.many else targets[0]
            updates.append({"uuid": h["uuid"], "properties": {p.ident: value}})
            names = [ctx.graph.require(t).get("title") for t in targets]
            rows.append((h.get("title", "")[:60], p.title, ", ".join(names)))

    print(f"{len(updates)} values to repair")
    for title, prop, target in sorted(rows, key=lambda r: (r[1], r[0])):
        print(f"  {prop:20} {target:12} ← {title}")
    if not updates:
        return

    r = dispatch(ctx, tools, "update_objects", {"updates": updates, "dry_run": True})
    if r.is_error:
        sys.exit(f"dry run failed: {r.content[0].text}")
    print(f"dry run ok: {len(json.loads(r.content[0].text)['updates'])} updates validated")
    if not a.apply:
        return
    done = 0
    for u in updates:  # one call per node: a failure stops with a clear position
        r = dispatch(ctx, tools, "update_objects", {"updates": [u]})
        out = json.loads(r.content[0].text) if not r.is_error else None
        if r.is_error or not out["verified"]:
            sys.exit(f"stopped after {done} repairs at {u['uuid']}: {r.content[0].text}")
        done += 1
    print(f"repaired {done} values, all verified")


if __name__ == "__main__":
    main()
