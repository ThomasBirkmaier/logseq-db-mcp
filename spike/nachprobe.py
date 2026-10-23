"""Nachproben zu F10/F11 (siehe docs/API-BEFUNDE.md). Nur in SpikeTest."""
import datetime as dt
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import spike as S  # noqa: E402

env = json.loads(S.CONFIG.read_text())["mcpServers"]["mcp-logseq"]["env"]
out = Path(__file__).parent / "out" / ("nachprobe_" + dt.datetime.now().strftime("%Y-%m-%d_%H%M%S"))
out.mkdir(parents=True)
ls = S.Logseq(env["LOGSEQ_API_TOKEN"], 12315, out / "calls.jsonl")
f0 = S.Finding("F0", "Umgebung")
S.guard(S.preflight(ls, f0), "SpikeTest", False)
run = dt.datetime.now().strftime("%H%M%S")
f = S.Finding("N", "Nachproben")

# 1. upsertNodes mit schlichtem Tag-Titel
S._mcp_upsert(ls, f, "upsertNodes Tag 'SpikeTag" + run + "'",
              [{"operation": "add", "entityType": "tag", "id": None, "data": {"title": "SpikeTag" + run}}])
t = S._by_title(ls, "SpikeTag" + run)
f.check("schlichter Tag angelegt", t is not None, (t or {}).get("ident"))

# 2. Seite per addBlockTag(Klasse Tag) zum Tag machen
TAG_CLASS = "00000002-5389-0208-3000-000000000000"
ok, page, _ = ls.api("logseq.Editor.createPage", f"Spike {run} Seite wird Tag", {}, {"createFirstBlock": False})
ok, res, _ = ls.api("logseq.Editor.addBlockTag", S._uuid(page), TAG_CLASS)
p = S._by_title(ls, f"Spike {run} Seite wird Tag")
f.check("Seite + addBlockTag(Tag) ergibt eine Klasse", ok and bool(p and p.get("ident")), f"call={res} ident={(p or {}).get('ident')}")
if p and p.get("ident"):
    parent = S._by_title(ls, "SpikeTag" + run) or {}
    ok, res, _ = ls.api("logseq.Editor.addTagExtends", str(p["uuid"]), str(parent.get("uuid"))) if parent else (False, "kein Eltern-Tag", 0)
    p2 = S._by_title(ls, f"Spike {run} Seite wird Tag") or {}
    f.check("addTagExtends an dieser Klasse", ok and bool(p2.get(":logseq.property.class/extends")), p2.get(":logseq.property.class/extends"))
    prop = S._by_title(ls, next((c for c in ["Spike UI Text"]), "")) or {}
    ok, res, _ = ls.api("logseq.Editor.addTagProperty", str(p["uuid"]), str(prop.get("uuid")))
    p3 = S._by_title(ls, f"Spike {run} Seite wird Tag") or {}
    f.check("addTagProperty(user.property) an dieser Klasse", ok and bool(p3.get(":logseq.property.class/properties")),
            p3.get(":logseq.property.class/properties"))

# 3. Auswahlwerte aus Knoten-UUIDs
_, props, _ = ls.api("logseq.Editor.getAllProperties")
ch = next((x for x in props if str(x.get("title", "")).endswith(" Auswahl")), None)
f.check("Auswahl-Property aus F11 gefunden", ch is not None, (ch or {}).get("ident"))
if ch:
    ident, puuid = ch["ident"], ch["uuid"]
    uu = {}
    for name in ["Rot", "Grün"]:
        ok, pg, _ = ls.api("logseq.Editor.createPage", f"Spike {run} {name}", {}, {"createFirstBlock": False})
        uu[name] = S._uuid(pg)
    ok, res, _ = ls.api("logseq.Editor.addPropertyValueChoices", puuid, list(uu.values()))
    f.check("addPropertyValueChoices mit Seiten-UUIDs", ok, res)
    q = f'[:find (pull ?v [*]) :where [?p :db/ident {ident}] [?v :block/closed-value-property ?p]]'
    ok, vals, _ = ls.api("logseq.DB.datascriptQuery", q)
    closed = [v[0] for v in vals or []] if ok else []
    f.check("Auswahlwerte angelegt", bool(closed), [(v.get("title"), v.get("id"), v.get("uuid")) for v in closed] or vals)
    if closed:
        first = closed[0]
        for form, value, opts in [("Entitäts-ID", first["id"], {"entityId": True}), ("Titel", first.get("title"), {})]:
            ok, b, _ = ls.api("logseq.Editor.appendBlockInPage", f"Spike {run} Seite wird Tag", f"Auswahl per {form}")
            ok, res, _ = ls.api("logseq.Editor.upsertBlockProperty", S._uuid(b), ident, value, opts)
            raw = (S._raw(ls, S._uuid(b)) or {}).get(ident)
            f.check(f"Auswahl per {form}", ok and isinstance(raw, dict) and raw.get("id") == first["id"],
                    f"call={'ok' if ok else res} roh={raw}, erwartet id {first['id']}")
        ok, vals2, _ = ls.api("logseq.DB.datascriptQuery", q)
        f.check("Anzahl Auswahlwerte danach", None, len(vals2 or []))

S.report(out / "report.md", [f], run, "SpikeTest")
print((out / "report.md").read_text())
