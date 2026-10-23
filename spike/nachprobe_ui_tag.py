"""Nachprobe (docs/KONZEPT.md Abschnitt 8, Punkt 1): Lässt sich das Schema eines Tags aus der
Oberfläche (user.class) über die API ändern? Nur in SpikeTest; braucht dort den Tag "Spike UI Tag".

    uv run --no-editable python spike/nachprobe_ui_tag.py
"""

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from logseq_db_mcp.client import LogseqError  # noqa: E402
from logseq_db_mcp.settings import Settings  # noqa: E402
from logseq_db_mcp.tools.base import Context  # noqa: E402

CONFIG = Path.home() / "Library/Application Support/Claude/claude_desktop_config.json"
token = next(s["env"]["LOGSEQ_API_TOKEN"] for s in json.loads(CONFIG.read_text())["mcpServers"].values()
             if "LOGSEQ_API_TOKEN" in s.get("env", {}))
ctx = Context.create(Settings(api_key=token, base_url="http://127.0.0.1:12315", verify_ssl=False,
                              connect_timeout=3, read_timeout=60))
c, g = ctx.client, ctx.graph
graph = c.call("logseq.App.getCurrentGraph")["name"]
if graph != "logseq_db_SpikeTest":
    sys.exit(f"ABBRUCH: offen ist {graph}, nicht SpikeTest")

run = time.strftime("%H%M%S")
results = []


def check(label, ok, detail=""):
    results.append((label, ok, detail))
    print(f"{'ja   ' if ok else 'nein '} {label}: {str(detail)[:200]}")


def attempt(label, fn):
    try:
        return True, fn()
    except LogseqError as e:
        return False, str(e)


def fresh(entity_uuid):
    return g.require(entity_uuid)


def ids(e, key):
    v = e.get(key) or []
    return {x["id"] for x in (v if isinstance(v, list) else [v]) if isinstance(x, dict)}


tags = g.entities_by_title("Spike UI Tag")
if not tags:
    sys.exit("ABBRUCH: kein Tag 'Spike UI Tag' in SpikeTest")
tag = tags[0]
check("Spike UI Tag ist eine user.class", str(tag.get("ident", "")).startswith(":user.class/"), tag.get("ident"))

# Properties für die Versuche, als user.property wie aus der Oberfläche
names = {k: f"UIT {run} {k}" for k in ("A", "B", "C", "D", "E")}
c.mcp_tool("upsertNodes", {"operations": [
    {"operation": "add", "entityType": "property", "id": None,
     "data": {"title": t, "property-type": "node" if k in "CD" else "default", "property-cardinality": "many" if k in "CD" else "one"}}
    for k, t in names.items()]})
ctx.schema.invalidate()
P = {k: ctx.schema.property(t) for k, t in names.items()}
CP = ":logseq.property.class/properties"

# 1. addTagProperty an der user.class, mit verschiedenen Schlüsseln
for form, tkey, pkey in [("UUID/UUID", tag["uuid"], P["A"].uuid), ("Ident/Ident", tag["ident"].lstrip(":"), P["A"].ident.lstrip(":")),
                         ("Name/Name", "Spike UI Tag", names["A"])]:
    ok, res = attempt(form, lambda: c.call("logseq.Editor.addTagProperty", tkey, pkey))
    hit = P["A"].id in ids(fresh(tag["uuid"]), CP)
    check(f"addTagProperty {form}", ok and hit, res)
    if hit:
        break

# 2. upsertNodes edit am Tag: class-properties
for form, value in [("UUID", P["B"].uuid), ("Name", names["B"])]:
    ok, res = attempt(form, lambda: c.mcp_tool("upsertNodes", {"operations": [
        {"operation": "edit", "entityType": "tag", "id": tag["uuid"], "data": {"class-properties": [value]}}]}))
    after = fresh(tag["uuid"])
    hit = P["B"].id in ids(after, CP)
    check(f"upsertNodes edit tag class-properties per {form}", ok and hit, res)
    check(f"  … und A bleibt dabei erhalten ({form})", P["A"].id in ids(after, CP) or P["A"].id not in ids(tag, CP), sorted(ids(after, CP)))
    if hit:
        break

# 3. setPropertyNodeTags: erlaubte Klassen einer node-Property
for form, value in [("Entitäts-ID", [tag["id"]]), ("UUID", [tag["uuid"]])]:
    ok, res = attempt(form, lambda: c.call("logseq.Editor.setPropertyNodeTags", P["C"].uuid, value))
    hit = tag["id"] in ids(g.require(P["C"].uuid), ":logseq.property/classes")
    check(f"setPropertyNodeTags per {form}", ok and hit, res)
    if hit:
        break

# 4. upsertNodes edit an der Property: property-classes
for form, value in [("UUID", tag["uuid"]), ("Name", "Spike UI Tag")]:
    ok, res = attempt(form, lambda: c.mcp_tool("upsertNodes", {"operations": [
        {"operation": "edit", "entityType": "property", "id": P["D"].uuid, "data": {"property-classes": [value]}}]}))
    hit = tag["id"] in ids(g.require(P["D"].uuid), ":logseq.property/classes")
    check(f"upsertNodes edit property property-classes per {form}", ok and hit, res)
    if hit:
        break

# 5. removeTagProperty an der user.class
ok, res = attempt("remove", lambda: c.call("logseq.Editor.removeTagProperty", tag["uuid"], P["A"].uuid))
check("removeTagProperty", ok and P["A"].id not in ids(fresh(tag["uuid"]), CP), res)

# 6. Vererbung: addTagExtends user.class → neue API-Klasse, danach wieder entfernen
base = c.call("logseq.Editor.createTag", f"UIT {run} Basis")
ok, res = attempt("extends", lambda: c.call("logseq.Editor.addTagExtends", tag["uuid"], base["uuid"]))
hit = base["id"] in ids(fresh(tag["uuid"]), ":logseq.property.class/extends")
check("addTagExtends user.class → API-Klasse", ok and hit, res)
if hit:
    ok, res = attempt("unextend", lambda: c.call("logseq.Editor.removeTagExtends", tag["uuid"], base["uuid"]))
    check("removeTagExtends", ok and base["id"] not in ids(fresh(tag["uuid"]), ":logseq.property.class/extends"), res)

# 7. Rename einer user.property (Titel ändern, Ident bleibt)
new_title = f"UIT {run} E umbenannt"
ok, res = attempt("rename", lambda: c.mcp_tool("upsertNodes", {"operations": [
    {"operation": "edit", "entityType": "property", "id": P["E"].uuid, "data": {"title": new_title}}]}))
e = g.require(P["E"].uuid)
check("upsertNodes edit property title", ok and e.get("title") == new_title, f"{res} → {e.get('title')}, ident {e.get('ident')}")

print("\nZusammenfassung:")
for label, ok, _ in results:
    print(f"  {'ja  ' if ok else 'nein'}  {label}")
