"""Spike (Phase 0, docs/KONZEPT.md Abschnitt 8): Was tut die HTTP-API eines
DB-Graphen wirklich?

Schreibt nur in einen leeren Testgraphen. Der Name des offenen Graphen muss
mit --graph übereinstimmen, ReqSeq wird immer abgewiesen. Alles, was der
Spike anlegt, beginnt mit "Spike <Lauf-ID>". Es wird nichts gelöscht.

    python -I spike/spike.py --preflight            # nur lesend, beliebiger Graph
    python -I spike/spike.py --graph SpikeTest      # alle Versuche im Testgraphen

Ergebnis: spike/out/<Zeitstempel>/report.md (Befunde) und calls.jsonl (jeder
Aufruf mit Antwort, Grundlage für die Contract-Fixtures).
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import time
import traceback
from pathlib import Path
from urllib.parse import urlparse

import requests

CONFIG = Path.home() / "Library/Application Support/Claude/claude_desktop_config.json"
FORBIDDEN_GRAPHS = ("reqseq",)
MAX_USER_PAGES = 30  # mehr eigene Seiten → kein leerer Testgraph


# ---------------------------------------------------------------------------
# Verbindung
# ---------------------------------------------------------------------------


class Logseq:
    """HTTP-API (/api) und eingebauter MCP-Server (/mcp), mit Mitschnitt."""

    def __init__(self, token: str, port: int, log: Path):
        # 127.0.0.1 statt localhost: /mcp weist "Host: localhost" ab.
        self.base = f"http://127.0.0.1:{port}"
        self.h = {"Authorization": f"Bearer {token}"}
        self.log = log.open("a", encoding="utf-8")
        self.mcp_sid: str | None = None
        self.mcp_id = 0

    def _record(self, kind, name, args, status, result, ms):
        self.log.write(json.dumps({"kind": kind, "name": name, "args": args, "status": status,
                                   "result": result, "ms": ms}, ensure_ascii=False, default=str) + "\n")
        self.log.flush()

    def api(self, method: str, *args):
        """Ein Aufruf der HTTP-API. Gibt (ok, Ergebnis oder Fehlertext, ms) zurück."""
        t = time.perf_counter()
        try:
            r = requests.post(self.base + "/api", headers=self.h,
                              json={"method": method, "args": list(args)}, timeout=60)
            ms = round((time.perf_counter() - t) * 1000)
            try:
                body = r.json()
            except ValueError:
                body = r.text
            ok = r.status_code == 200 and not (isinstance(body, dict) and "error" in body and len(body) == 1)
        except requests.RequestException as e:
            ms, ok, body = round((time.perf_counter() - t) * 1000), False, f"{type(e).__name__}: {e}"
        self._record("api", method, list(args), ok, body, ms)
        return ok, body, ms

    def _mcp_post(self, msg):
        h = {**self.h, "Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
        if self.mcp_sid:
            h["Mcp-Session-Id"] = self.mcp_sid
        r = requests.post(self.base + "/mcp", headers=h, json=msg, timeout=300)
        if "mcp-session-id" in r.headers:
            self.mcp_sid = r.headers["mcp-session-id"]
        text = r.text
        if "data:" in text:
            text = [l[5:] for l in text.splitlines() if l.startswith("data:")][-1]
        return json.loads(text) if text.strip() else None

    def mcp(self, tool: str, arguments: dict):
        """Ein Tool-Aufruf am eingebauten MCP-Server. Gibt (ok, Text, ms) zurück."""
        if self.mcp_sid is None:
            self._mcp_post({"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {
                "protocolVersion": "2025-06-18", "capabilities": {},
                "clientInfo": {"name": "spike", "version": "0"}}})
            self._mcp_post({"jsonrpc": "2.0", "method": "notifications/initialized"})
        self.mcp_id += 1
        t = time.perf_counter()
        try:
            b = self._mcp_post({"jsonrpc": "2.0", "id": self.mcp_id, "method": "tools/call",
                                "params": {"name": tool, "arguments": arguments}})
            res = (b or {}).get("result", {})
            text = (res.get("content") or [{}])[0].get("text", json.dumps(b))
            ok = not res.get("isError") and "error" not in (b or {})
        except Exception as e:
            ok, text = False, f"{type(e).__name__}: {e}"
        ms = round((time.perf_counter() - t) * 1000)
        self._record("mcp", tool, arguments, ok, text, ms)
        return ok, text, ms


# ---------------------------------------------------------------------------
# Befunde
# ---------------------------------------------------------------------------


class Finding:
    def __init__(self, key: str, question: str):
        self.key, self.question = key, question
        self.rows: list[tuple[str, str, str]] = []  # (Prüfung, Ergebnis, Detail)
        self.notes: list[str] = []

    def check(self, label: str, result, detail="") -> None:
        mark = {True: "ja", False: "nein", None: "unklar"}.get(result, str(result))
        self.rows.append((label, mark, _short(detail)))

    def note(self, text: str) -> None:
        self.notes.append(text)


def _short(v, n=220) -> str:
    s = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False, default=str)
    s = s.replace("\n", " ").replace("|", "\\|")
    return s if len(s) <= n else s[:n] + " …"


def _uuid(entity) -> str | None:
    if isinstance(entity, dict):
        return entity.get("uuid") or entity.get("block/uuid")
    return None


def _eid(entity) -> int | None:
    return entity.get("id") if isinstance(entity, dict) else None


# ---------------------------------------------------------------------------
# Versuche
# ---------------------------------------------------------------------------


def preflight(ls: Logseq, f: Finding) -> dict:
    ok, graph, ms = ls.api("logseq.App.getCurrentGraph")
    f.check("HTTP-API erreichbar", ok, f"{ms} ms")
    f.check("Offener Graph", graph.get("name") if ok and graph else None)
    ok, is_db, _ = ls.api("logseq.App.checkCurrentIsDbGraph")
    f.check("DB-Graph (checkCurrentIsDbGraph)", bool(is_db) if ok else None, is_db)
    ok, pages, ms = ls.api("logseq.Editor.getAllPages")
    # getAllPages liefert nur Titel und IDs; eingebaute Seiten erkennt man an
    # der festen UUID-Vorsilbe 00000002- (Befund aus ReqSeq).
    user_pages = [p for p in pages or [] if isinstance(p, dict)
                  and not str(p.get("uuid", "")).startswith("00000002-")] if ok else []
    f.check("getAllPages", ok, f"{len(pages or [])} Seiten, davon {len(user_pages)} eigene, {ms} ms")
    for m in ["logseq.Editor.getAllTags", "logseq.Editor.getAllProperties"]:
        ok, v, ms = ls.api(m)
        f.check(m, ok, f"{len(v) if isinstance(v, list) else v}, {ms} ms")
    ok, v, ms = ls.api("logseq.DB.datascriptQuery", "[:find (count ?b) :where [?b :block/uuid]]")
    f.check("datascriptQuery (Anzahl Entitäten)", ok, f"{v}, {ms} ms")
    ok, v, ms = ls.mcp("listTags", {})
    f.check("Eingebauter MCP (/mcp) erreichbar", ok, f"{ms} ms")
    return {"graph": graph if isinstance(graph, dict) else {}, "is_db": bool(is_db), "user_pages": user_pages}


def guard(info: dict, expected: str, force: bool) -> None:
    name = str(info["graph"].get("name", ""))
    short = name.removeprefix("logseq_db_")
    if any(g in name.lower() for g in FORBIDDEN_GRAPHS):
        sys.exit(f"ABBRUCH: offener Graph ist '{name}'. Der Spike schreibt nie in ReqSeq.")
    if short != expected and name != expected:
        sys.exit(f"ABBRUCH: offener Graph ist '{name}', erwartet '{expected}'.")
    if not info["is_db"]:
        sys.exit("ABBRUCH: offener Graph ist kein DB-Graph.")
    if len(info["user_pages"]) > MAX_USER_PAGES and not force:
        sys.exit(f"ABBRUCH: {len(info['user_pages'])} eigene Seiten, das ist kein leerer Testgraph (--force übergeht das).")


PROP_TYPES = [  # (Suffix, Schema, Testwert, Wert für Kardinalität many)
    ("Text", {"type": "default"}, "Hallo Spike", None),
    ("Number", {"type": "number"}, 42, None),
    ("Checkbox", {"type": "checkbox"}, True, None),
    ("Url", {"type": "url"}, "https://example.org/spike", None),
    ("Date", {"type": "date"}, None, None),          # Wert: Journal-Entität, siehe unten
    ("Node", {"type": "node"}, None, None),          # Wert: Seite, siehe unten
    ("NodeMany", {"type": "node", "cardinality": "many"}, None, None),
]


def properties_via_api(ls: Logseq, f: Finding, run: str) -> dict:
    """upsertProperty mit Schema: Welcher Namespace entsteht, wirkt das Schema?"""
    created = {}
    for suffix, schema, *_ in PROP_TYPES:
        key = f"spike{run}{suffix}"
        ok, v, ms = ls.api("logseq.Editor.upsertProperty", key, schema, {"name": f"Spike {run} API {suffix}"})
        ok2, prop, _ = ls.api("logseq.Editor.getProperty", key)
        ident = prop.get("ident") if ok2 and isinstance(prop, dict) else None
        ptype = prop.get(":logseq.property/type") if isinstance(prop, dict) else None
        card = (prop.get(":db/cardinality") or prop.get("cardinality")) if isinstance(prop, dict) else None
        f.check(f"upsertProperty {suffix}", ok, f"ident={ident} type={ptype} card={card} ({ms} ms)")
        if ok2 and isinstance(prop, dict):
            created[suffix] = {"key": key, "ident": ident, "uuid": _uuid(prop), "entity": prop}
    idents = [p["ident"] for p in created.values() if p["ident"]]
    f.check("Namespace der API-Properties", None if not idents else idents[0].split("/")[0], idents[:3])
    return created


def properties_via_mcp(ls: Logseq, f: Finding, run: str) -> dict:
    """upsertNodes des eingebauten MCP: entstehen user.property-Idents? Wie lange dauert es?"""
    ops = [{"operation": "add", "entityType": "property", "id": None,
            "data": {"title": f"Spike {run} MCP {suffix}", "property-type": schema["type"],
                     "property-cardinality": schema.get("cardinality", "one")}}
           for suffix, schema, *_ in PROP_TYPES if suffix in ("Text", "Number", "Node", "NodeMany")]
    ok, text, ms = ls.mcp("upsertNodes", {"operations": ops, "dry-run": True})
    f.check("upsertNodes dry-run (4 Properties)", ok, f"{ms} ms: {text}")
    ok, text, ms = ls.mcp("upsertNodes", {"operations": ops})
    f.check("upsertNodes (4 Properties)", ok, f"{ms} ms: {text}")
    ok, props, _ = ls.api("logseq.Editor.getAllProperties")
    created = {}
    for p in props if ok else []:
        title = p.get("title") or p.get("rawTitle") or ""
        if title.startswith(f"Spike {run} MCP "):
            created[title.rsplit(" ", 1)[1]] = {"key": p.get("ident"), "ident": p.get("ident"), "uuid": _uuid(p), "entity": p}
    f.check("Namespace der MCP-Properties", None if not created else next(iter(created.values()))["ident"],
            [c["ident"] for c in created.values()])
    return created


def ui_properties(ls: Logseq, f: Finding) -> dict:
    """Vom Nutzer in der Oberfläche angelegte Properties 'Spike UI <Typ>' (optional)."""
    ok, props, _ = ls.api("logseq.Editor.getAllProperties")
    found = {}
    for p in props if ok else []:
        title = p.get("title") or p.get("rawTitle") or ""
        if title.startswith("Spike UI "):
            found[title.removeprefix("Spike UI ")] = {"key": p.get("ident"), "ident": p.get("ident"), "uuid": _uuid(p), "entity": p}
    f.check("UI-Properties 'Spike UI …' gefunden", bool(found), {k: v["ident"] for k, v in found.items()})
    if not found:
        f.note("Keine UI-Properties gefunden: Ob user.property-Werte schreibbar sind, ist dann nur über die MCP-Properties belegt.")
    return found


def write_values(ls: Logseq, f: Finding, run: str, sets: dict[str, dict]) -> None:
    """Kernfrage 1 und 2: Werte schreiben, je Property-Herkunft, Schlüsselform und Typ."""
    ok, page, _ = ls.api("logseq.Editor.createPage", f"Spike {run} Werte", {}, {"createFirstBlock": False})
    ok, target, _ = ls.api("logseq.Editor.createPage", f"Spike {run} Zielseite", {}, {"createFirstBlock": False})
    ok, journal, _ = ls.api("logseq.Editor.createJournalPage", "2030-01-15")
    f.check("createJournalPage 2030-01-15", ok, {"uuid": _uuid(journal), "id": _eid(journal)} if ok else journal)
    for origin, props in sets.items():
        for suffix, p in props.items():
            if suffix not in {s for s, *_ in PROP_TYPES}:
                f.check(f"{origin} {suffix}", None, "unbekannter Typ-Suffix, übersprungen")
                continue
            ok, block, _ = ls.api("logseq.Editor.appendBlockInPage", f"Spike {run} Werte", f"{origin} {suffix}")
            buuid = _uuid(block)
            if not buuid:
                f.check(f"{origin} {suffix}: Block anlegen", False, block)
                continue
            value, opts = _value_for(suffix, target, journal)
            ident = p["ident"] or ""
            short = ident.lstrip(":")
            for key_form, key in [("Ident", ident), ("Ident ohne ':'", short), ("Schlüssel", p["key"])]:
                if not key:
                    continue
                ok, res, ms = ls.api("logseq.Editor.upsertBlockProperty", buuid, key, value, opts)
                ok2, props_back, _ = ls.api("logseq.Editor.getBlockProperties", buuid)
                stored = _find_value(props_back, ident)
                f.check(f"{origin} {suffix} via {key_form}", bool(ok and stored is not None),
                        f"call={'ok' if ok else res} back={stored} ({ms} ms)")
                if ok and stored is not None:
                    break
            # Rohform in der DB, unabhängig von getBlockProperties
            q = f'[:find (pull ?b [*]) :where [?b :block/uuid #uuid "{buuid}"]]'
            okq, raw, _ = ls.api("logseq.DB.datascriptQuery", q)
            if okq and raw and ident:
                f.note(f"{origin} {suffix}: Rohwert {ident} = {_short((raw[0][0] or {}).get(ident), 160)}")


def _value_for(suffix: str, target, journal):
    if suffix == "Date":
        return _eid(journal), {"entityId": True}
    if suffix in ("Node",):
        return _eid(target), {"entityId": True}
    if suffix == "NodeMany":
        return [_eid(target), _eid(journal)], {"entityId": True}
    return next(v for s, _, v, _ in PROP_TYPES if s == suffix), {}


def _find_value(props, ident: str):
    if not isinstance(props, dict) or not ident:
        return None
    for k in (ident, ident.lstrip(":"), ident.split("/")[-1]):
        if k in props:
            return props[k]
    return None


def tags(ls: Logseq, f: Finding, run: str) -> None:
    """Kernfrage 4: Tags, Vererbung, Klassen-Properties, Objekte einer Klasse."""
    a_name, b_name = f"Spike {run} Klasse A", f"Spike {run} Klasse B"
    ok, a, ms = ls.api("logseq.Editor.createTag", a_name)
    f.check("createTag A", ok, f"ident={a.get('ident') if isinstance(a, dict) else a} ({ms} ms)")
    ok, b, _ = ls.api("logseq.Editor.createTag", b_name, {"tagProperties": [
        {"name": f"spike{run}TagProp", "schema": {"type": "number"}}]})
    f.check("createTag B mit tagProperties", ok, b.get("ident") if isinstance(b, dict) else b)
    ok, _, _ = ls.api("logseq.Editor.addTagExtends", _uuid(b), _uuid(a))
    ok2, b2, _ = ls.api("logseq.Editor.getTag", _uuid(b))
    f.check("addTagExtends B → A", ok, _short((b2 or {}).get(":logseq.property.class/extends") if isinstance(b2, dict) else b2))
    f.check("tagProperties an B angekommen", None if not isinstance(b2, dict) else bool(b2.get(":logseq.property.class/properties")),
            (b2 or {}).get(":logseq.property.class/properties") if isinstance(b2, dict) else b2)
    ok, page, _ = ls.api("logseq.Editor.createPage", f"Spike {run} Objekte", {}, {"createFirstBlock": False})
    ok, blk, _ = ls.api("logseq.Editor.appendBlockInPage", f"Spike {run} Objekte", "Objekt der Klasse B")
    ok, _, _ = ls.api("logseq.Editor.addBlockTag", _uuid(blk), _uuid(b))
    ok2, back, _ = ls.api("logseq.Editor.getBlock", _uuid(blk))
    f.check("addBlockTag", ok, (back or {}).get("tags") if isinstance(back, dict) else back)
    for label, tag in [("A", a), ("B", b)]:
        for form, key in [("Name", tag.get("title") or tag.get("rawTitle")), ("Ident", tag.get("ident")),
                          ("Ident ohne ':'", str(tag.get("ident", "")).lstrip(":")), ("UUID", _uuid(tag)), ("id", _eid(tag))]:
            ok, objs, _ = ls.api("logseq.Editor.getTagObjects", key)
            hit = ok and isinstance(objs, list) and any(_uuid(o) == _uuid(blk) for o in objs)
            f.check(f"getTagObjects({label}) per {form} findet das Objekt" + (" (Unterklasse)" if label == "A" else ""),
                    hit, objs if not ok else len(objs or []))
    q = ('[:find [?u ...] :in $ ?tu :where [?t :block/uuid ?tu] '
         '(or-join [?b ?t] [?b :block/tags ?t] (and [?c :logseq.property.class/extends ?t] [?b :block/tags ?c])) [?b :block/uuid ?u]]')
    ok, res, ms = ls.api("logseq.DB.datascriptQuery", q, f'#uuid "{_uuid(a)}"')
    f.check("Datascript: Objekte von A inkl. Unterklassen (Eingabe als #uuid)", ok and bool(res), f"{res} ({ms} ms)")
    if not ok or not res:
        q2 = q.replace(":in $ ?tu ", "").replace("[?t :block/uuid ?tu]", f'[?t :block/uuid #uuid "{_uuid(a)}"]')
        ok, res, ms = ls.api("logseq.DB.datascriptQuery", q2)
        f.check("… mit eingebetteter UUID statt :in", ok and bool(res), f"{res} ({ms} ms)")


def batch_insert(ls: Logseq, f: Finding, run: str) -> None:
    """Kernfrage 3: insertBatchBlock mit Properties im DB-Graphen; Aufwand 20 Blöcke."""
    title = f"Spike {run} Batch"
    ok, page, _ = ls.api("logseq.Editor.createPage", title, {}, {"createFirstBlock": True})
    ok, tree, _ = ls.api("logseq.Editor.getPageBlocksTree", title)
    f.check("createPage mit createFirstBlock liefert einen Block", bool(ok and tree), tree)
    ok, first, _ = ls.api("logseq.Editor.appendBlockInPage", title, "Anker")
    anchor = _uuid(first)

    def count():
        ok, t, _ = ls.api("logseq.Editor.getPageBlocksTree", title)
        def n(bs):
            return sum(1 + n(b.get("children") or []) for b in bs or [])
        return n(t) if ok else None
    custom = "6a000000-0000-4000-8000-0000000000aa"
    batch = [{"content": "Batch 1", "properties": {"id": custom}},
             {"content": "Batch 2 mit Kindern", "children": [{"content": "Kind 2.1"}, {"content": "Kind 2.2"}]}]
    ok, res, ms = ls.api("logseq.Editor.insertBatchBlock", anchor, batch, {"sibling": True, "keepUUID": True})
    f.check("insertBatchBlock mit Kindern legt 4 Blöcke an", ok and count() == 5, f"Blöcke auf der Seite: {count()}, Antwort {_short(res, 80)} ({ms} ms)")
    ok2, got, _ = ls.api("logseq.Editor.getBlock", custom)
    f.check("keepUUID über properties.id", ok2 and bool(got), _uuid(got) if isinstance(got, dict) else got)
    many = [{"content": f"Massenblock {i}"} for i in range(20)]
    before = count()
    ok, _, ms_batch = ls.api("logseq.Editor.insertBatchBlock", anchor, many, {"sibling": True})
    mid = count()
    t = time.perf_counter()
    for i in range(20):
        ls.api("logseq.Editor.appendBlockInPage", title, f"Einzelblock {i}")
    ms_single = round((time.perf_counter() - t) * 1000)
    f.check("20 Blöcke: insertBatchBlock vs. 20 × appendBlockInPage", ok and mid - before == 20 and count() - mid == 20,
            f"{ms_batch} ms vs. {ms_single} ms; Zuwachs {mid - before} / {count() - mid}")
    ok, res, _ = ls.api("logseq.Editor.insertBlock", None, "ohne Anker")
    f.check("insertBlock ohne Anker meldet Fehler", not ok, res)
    ok, blk, ms = ls.api("logseq.Editor.insertBlock", anchor, "insertBlock mit customUUID",
                         {"sibling": True, "customUUID": "6a000000-0000-4000-8000-0000000000bb"})
    f.check("insertBlock customUUID", ok and _uuid(blk) == "6a000000-0000-4000-8000-0000000000bb", _uuid(blk))


def move(ls: Logseq, f: Finding, run: str) -> None:
    """Kernfrage 5: Semantik von moveBlock."""
    title = f"Spike {run} Verschieben"
    ok, _, _ = ls.api("logseq.Editor.createPage", title, {}, {"createFirstBlock": False})
    uu = {}
    for n in ["P", "Q"]:
        ok, b, _ = ls.api("logseq.Editor.appendBlockInPage", title, n)
        uu[n] = _uuid(b)
    for n in ["c1", "c2", "c3"]:
        ok, b, _ = ls.api("logseq.Editor.insertBlock", uu["P"], n, {"sibling": False, "end": True})
        uu[n] = _uuid(b)

    def order(parent):
        ok, b, _ = ls.api("logseq.Editor.getBlock", uu[parent], {"includeChildren": True})
        return [c.get("title") or c.get("content") for c in (b or {}).get("children", [])] if ok else b

    f.check("Ausgangsordnung unter P", None, order("P"))
    ls.api("logseq.Editor.moveBlock", uu["c3"], uu["c1"], {"before": True})
    f.check("c3 vor c1 (before)", None, order("P"))
    ls.api("logseq.Editor.moveBlock", uu["c3"], uu["c1"], {"before": True})
    f.check("dieselbe Verschiebung noch einmal (No-op?)", None, order("P"))
    ls.api("logseq.Editor.moveBlock", uu["c1"], uu["Q"], {"children": True})
    f.check("c1 als Kind von Q (children) → Q", None, order("Q"))
    ls.api("logseq.Editor.moveBlock", uu["c2"], uu["Q"], {"children": True})
    f.check("c2 als weiteres Kind von Q: voran oder hinten?", None, order("Q"))


def failures(ls: Logseq, f: Finding, run: str) -> None:
    """Meldet die API Erfolg ohne Wirkung?"""
    ghost = "6a000000-0000-4000-8000-00000000dead"
    ok, res, _ = ls.api("logseq.Editor.updateBlock", ghost, "gibt es nicht")
    f.check("updateBlock auf unbekannte UUID meldet Fehler", not ok, res)
    ok, blk, _ = ls.api("logseq.Editor.appendBlockInPage", f"Spike {run} Werte", "Fehlerfall")
    ok, res, _ = ls.api("logseq.Editor.upsertBlockProperty", _uuid(blk), f"spike{run}GibtEsNicht", "x")
    ok2, back, _ = ls.api("logseq.Editor.getBlockProperties", _uuid(blk))
    f.check("upsertBlockProperty mit unbekanntem Schlüssel meldet Fehler", not ok, f"call={res} back={back}")
    f.note("Legt der unbekannte Schlüssel stillschweigend eine Property an? Siehe 'back' und getAllProperties im Mitschnitt.")


def timings(ls: Logseq, f: Finding) -> None:
    for label, q in [("Alle Tags mit Objektzahl", '[:find ?t (count ?b) :where [?c :block/title "Tag"] [?t :block/tags ?c] [?b :block/tags ?t]]'),
                     ("Alle Blöcke mit Property-Wert (pull *)", "[:find (pull ?b [*]) :where [?b :block/page]]")]:
        ok, res, ms = ls.api("logseq.DB.datascriptQuery", q)
        f.check(f"Datascript: {label}", ok, f"{len(res) if isinstance(res, list) else res} Zeilen, {ms} ms")


def _by_title(ls: Logseq, title: str):
    """Entität mit genau diesem Titel (pull *), oder None."""
    ok, res, _ = ls.api("logseq.DB.datascriptQuery",
                        f'[:find (pull ?e [*]) :where [?e :block/title {json.dumps(title)}]]')
    return res[0][0] if ok and res else None


def _raw(ls: Logseq, uuid: str):
    ok, res, _ = ls.api("logseq.DB.datascriptQuery", f'[:find (pull ?b [*]) :where [?b :block/uuid #uuid "{uuid}"]]')
    return res[0][0] if ok and res else None


def _mcp_upsert(ls: Logseq, f: Finding, label: str, ops: list) -> bool:
    """upsertNodes erst als dry-run, dann echt. Gibt zurück, ob der echte Lauf gelang."""
    ok, text, ms = ls.mcp("upsertNodes", {"operations": ops, "dry-run": True})
    ok = ok and not str(text).startswith("API Error")
    f.check(f"{label}: dry-run", ok, f"{ms} ms: {text}")
    if not ok:
        return False
    ok, text, ms = ls.mcp("upsertNodes", {"operations": ops})
    ok = ok and not str(text).startswith("API Error")  # Fehler kommen als Text, nicht als isError
    f.check(f"{label}: echt", ok, f"{ms} ms: {text}")
    return ok


def mcp_tags(ls: Logseq, f: Finding, run: str) -> None:
    """Legt upsertNodes Tags als user.class an, mit class-extends und class-properties?"""
    prop, parent, child = f"Spike {run} TagProp", f"Spike {run} MCP Eltern", f"Spike {run} MCP Kind"
    ops = [
        {"operation": "add", "entityType": "property", "id": "tmp-prop",
         "data": {"title": prop, "property-type": "default", "property-cardinality": "one"}},
        {"operation": "add", "entityType": "tag", "id": "tmp-parent", "data": {"title": parent}},
        {"operation": "add", "entityType": "tag", "id": "tmp-child",
         "data": {"title": child, "class-extends": [parent], "class-properties": [prop]}},
    ]
    if not _mcp_upsert(ls, f, "Property + zwei Tags in einem Aufruf (Bezug per Name)", ops):
        # Rückfall schrittweise: einzeln anlegen, dann die Bezüge per UUID, dann per Name
        _mcp_upsert(ls, f, "Rückfall 1: nur Property", ops[:1])
        _mcp_upsert(ls, f, "Rückfall 2: nur Eltern-Tag", [ops[1]])
        p0, e0 = _by_title(ls, prop), _by_title(ls, parent)
        f.check("Property und Eltern-Tag existieren", bool(p0 and e0), [(p0 or {}).get("ident"), (e0 or {}).get("ident")])
        _mcp_upsert(ls, f, "Rückfall 3: Kind-Tag ohne Bezüge", [
            {"operation": "add", "entityType": "tag", "id": None, "data": {"title": child}}])
        k0 = _by_title(ls, child)
        if k0 and e0 and p0:
            ku = str(k0.get("uuid"))
            for form, ext, cp in [("UUID", [str(e0.get("uuid"))], [str(p0.get("uuid"))]), ("Name", [parent], [prop])]:
                _mcp_upsert(ls, f, f"Rückfall 4: edit class-extends per {form}", [
                    {"operation": "edit", "entityType": "tag", "id": ku, "data": {"class-extends": ext}}])
                _mcp_upsert(ls, f, f"Rückfall 5: edit class-properties per {form}", [
                    {"operation": "edit", "entityType": "tag", "id": ku, "data": {"class-properties": cp}}])
                k1 = _by_title(ls, child) or {}
                if k1.get(":logseq.property.class/extends") and k1.get(":logseq.property.class/properties"):
                    break
    p, e, k = _by_title(ls, prop), _by_title(ls, parent), _by_title(ls, child)
    f.check("Ident der Property", (p or {}).get("ident"), "")
    f.check("Ident des Eltern-Tags", (e or {}).get("ident"), "")
    f.check("Ident des Kind-Tags", (k or {}).get("ident"), "")
    ext = (k or {}).get(":logseq.property.class/extends")
    props = (k or {}).get(":logseq.property.class/properties")
    f.check("class-extends angekommen (Kind → Eltern)", bool(ext) and any(x.get("id") == (e or {}).get("id") for x in ext), ext)
    f.check("class-properties angekommen", bool(props) and any(x.get("id") == (p or {}).get("id") for x in props), props)
    if not (e and k):
        return
    # Nachträglich ändern: class-properties am Eltern-Tag per :edit
    euuid, kuuid = str(e.get("uuid")), str(k.get("uuid"))
    _mcp_upsert(ls, f, "edit: class-properties am Eltern-Tag", [
        {"operation": "edit", "entityType": "tag", "id": euuid, "data": {"class-properties": [prop]}}])
    e2 = _by_title(ls, parent)
    f.check("edit wirkt", bool((e2 or {}).get(":logseq.property.class/properties")),
            (e2 or {}).get(":logseq.property.class/properties"))
    # Objekt mit Kind-Tag anlegen, über Eltern-Ident finden
    ls.api("logseq.Editor.createPage", f"Spike {run} MCP Objekte", {}, {"createFirstBlock": False})
    page = _by_title(ls, f"Spike {run} MCP Objekte")
    obj = f"Spike {run} Objekt mit Kind-Tag"
    _mcp_upsert(ls, f, "Block mit Kind-Tag", [
        {"operation": "add", "entityType": "block", "id": None,
         "data": {"page-id": str(page.get("uuid")), "title": obj, "tags": [kuuid]}}])
    o = _by_title(ls, obj)
    ok, objs, _ = ls.api("logseq.Editor.getTagObjects", str(e.get("ident", "")).lstrip(":"))
    f.check("getTagObjects(Eltern) findet das Objekt", ok and o is not None and any(_uuid(x) == str(o.get("uuid")) for x in objs or []),
            objs if not ok else len(objs or []))
    # Property-Werte über upsertNodes? data erlaubt weitere Schlüssel
    ident = str((p or {}).get("ident", ""))
    for form, key in [("Titel", prop), ("Ident", ident), ("Ident ohne ':'", ident.lstrip(":"))]:
        title = f"Spike {run} Wert per upsertNodes ({form})"
        if _mcp_upsert(ls, f, f"Wert per upsertNodes, Schlüssel = {form}", [
                {"operation": "add", "entityType": "block", "id": None,
                 "data": {"page-id": str(page.get("uuid")), "title": title, key: "Wert über MCP"}}]):
            b = _by_title(ls, title)
            f.check(f"Wert ({form}) zurückgelesen", bool(b and ident in b), (b or {}).get(ident))


def datetime_and_choices(ls: Logseq, f: Finding, run: str) -> None:
    """datetime-Werte und feste Auswahlwerte (closed values)."""
    dt_name, ch_name = f"Spike {run} Zeitpunkt", f"Spike {run} Auswahl"
    _mcp_upsert(ls, f, "datetime- und Auswahl-Property (user.property)", [
        {"operation": "add", "entityType": "property", "id": None,
         "data": {"title": dt_name, "property-type": "datetime", "property-cardinality": "one"}},
        {"operation": "add", "entityType": "property", "id": None,
         "data": {"title": ch_name, "property-type": "default", "property-cardinality": "one"}}])
    pdt, pch = _by_title(ls, dt_name), _by_title(ls, ch_name)
    f.check("datetime-Property: Typ", (pdt or {}).get(":logseq.property/type"), (pdt or {}).get("ident"))
    title = f"Spike {run} Werte 2"
    ls.api("logseq.Editor.createPage", title, {}, {"createFirstBlock": False})

    def block(text):
        ok, b, _ = ls.api("logseq.Editor.appendBlockInPage", title, text)
        return _uuid(b)

    if pdt:
        ident = pdt["ident"]
        ms = 1918886400000  # 2030-10-23T00:00:00Z
        for form, value, opts in [("Epoch-ms", ms, {}), ("ISO-String", "2030-10-23T10:00:00Z", {})]:
            u = block(f"datetime {form}")
            ok, res, _ = ls.api("logseq.Editor.upsertBlockProperty", u, ident, value, opts)
            raw = (_raw(ls, u) or {}).get(ident)
            okb, back, _ = ls.api("logseq.Editor.getBlockProperties", u)
            f.check(f"datetime als {form}", ok and raw is not None, f"call={'ok' if ok else res} roh={raw} back={_find_value(back, ident)}")
    if not pch:
        return
    ident, puuid = pch["ident"], str(pch["uuid"])
    for form, choices in [("Strings", ["Rot", "Grün"])]:
        ok, res, _ = ls.api("logseq.Editor.addPropertyValueChoices", puuid, choices)
        f.check(f"addPropertyValueChoices mit {form}", ok, res)
    q = f'[:find (pull ?v [*]) :where [?p :db/ident {ident}] [?v :block/closed-value-property ?p]]'
    ok, vals, _ = ls.api("logseq.DB.datascriptQuery", q)
    closed = [v[0] for v in vals or []] if ok else []
    f.check("Auswahlwerte angelegt", bool(closed), [(v.get("title"), v.get("id")) for v in closed] if closed else vals)
    if not closed:
        f.note("Ohne Auswahlwerte über die API: in der Oberfläche anlegen und das Auswählen erneut prüfen.")
        return
    rot = next((v for v in closed if v.get("title") == "Rot"), closed[0])
    for form, value, opts in [("Entitäts-ID", rot["id"], {"entityId": True}), ("Titel als String", "Rot", {})]:
        u = block(f"Auswahl per {form}")
        ok, res, _ = ls.api("logseq.Editor.upsertBlockProperty", u, ident, value, opts)
        raw = (_raw(ls, u) or {}).get(ident)
        hit = isinstance(raw, dict) and raw.get("id") == rot["id"]
        f.check(f"Auswahl per {form} trifft den Auswahlwert", ok and hit, f"call={'ok' if ok else res} roh={raw} (Rot = {rot[':db/id']})")
    q2 = f'[:find (count ?v) :where [?p :db/ident {ident}] [?v :block/closed-value-property ?p]]'
    ok, n, _ = ls.api("logseq.DB.datascriptQuery", q2)
    f.check("Anzahl Auswahlwerte danach (2 = kein neuer angelegt)", None, n)


# ---------------------------------------------------------------------------
# Bericht
# ---------------------------------------------------------------------------


def report(path: Path, findings: list[Finding], run: str, graph: str) -> None:
    lines = [f"# Spike-Befunde, Lauf {run}", "",
             f"Graph `{graph}`, {dt.datetime.now().isoformat(timespec='seconds')}. "
             "Jeder Aufruf mit Antwort steht in `calls.jsonl`.", ""]
    for f in findings:
        lines += [f"## {f.key}: {f.question}", "", "| Prüfung | Ergebnis | Detail |", "|---|---|---|"]
        lines += [f"| {a} | {b} | {c} |" for a, b, c in f.rows]
        lines += [""] + [f"- {n}" for n in f.notes] + [""]
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--graph", help="Name des offenen Testgraphen (Pflicht außer bei --preflight)")
    ap.add_argument("--preflight", action="store_true", help="nur lesende Prüfungen, schreibt nichts")
    ap.add_argument("--force", action="store_true", help="Grenze für eigene Seiten übergehen")
    ap.add_argument("--only", help="nur diese Versuche, z. B. F5,F6 (F1 bis F3 laufen nur, wenn F4 dabei ist)")
    a = ap.parse_args()
    if not a.preflight and not a.graph:
        ap.error("--graph ist Pflicht (oder --preflight)")

    env = json.loads(CONFIG.read_text())["mcpServers"]["mcp-logseq"]["env"]
    port = urlparse(env.get("LOGSEQ_API_URL", "http://127.0.0.1:12315")).port or 12315
    run = dt.datetime.now().strftime("%m%d%H%M")
    out = Path(__file__).parent / "out" / dt.datetime.now().strftime("%Y-%m-%d_%H%M%S_%f")
    out.mkdir(parents=True)
    ls = Logseq(env["LOGSEQ_API_TOKEN"], port, out / "calls.jsonl")

    findings = []
    f0 = Finding("F0", "Umgebung (nur lesend)")
    findings.append(f0)
    info = preflight(ls, f0)
    graph = str(info["graph"].get("name", "?"))
    if a.preflight:
        report(out / "report.md", findings, run, graph)
        print(out / "report.md")
        return
    guard(info, a.graph, a.force)

    steps = [
        ("F1", "Properties über upsertProperty: Namespace und Schema", lambda f: properties_via_api(ls, f, run)),
        ("F2", "Properties über upsertNodes (eingebauter MCP): Namespace und Dauer", lambda f: properties_via_mcp(ls, f, run)),
        ("F3", "Properties aus der Oberfläche ('Spike UI …')", lambda f: ui_properties(ls, f)),
    ]
    only = set(a.only.split(",")) if a.only else None
    if only and "F4" not in only:
        steps = []
    sets = {}
    for key, q, fn in steps:
        f = Finding(key, q)
        findings.append(f)
        try:
            sets[key] = fn(f) or {}
        except Exception:
            f.note("Abbruch: " + _short(traceback.format_exc(), 600))
    origins = {"API": sets.get("F1", {}), "MCP": sets.get("F2", {}), "UI": sets.get("F3", {})}
    for key, q, fn in [
        ("F4", "Werte schreiben und zurücklesen (Kernfrage: user.property schreibbar?)", lambda f: write_values(ls, f, run, origins)),
        ("F5", "Tags, Vererbung, Klassen-Properties, Objekte einer Klasse", lambda f: tags(ls, f, run)),
        ("F6", "insertBatchBlock im DB-Graphen, Aufwand für 20 Blöcke", lambda f: batch_insert(ls, f, run)),
        ("F7", "moveBlock-Semantik", lambda f: move(ls, f, run)),
        ("F8", "Fehlerfälle: Erfolg ohne Wirkung?", lambda f: failures(ls, f, run)),
        ("F9", "Antwortzeiten von Datascript", lambda f: timings(ls, f)),
        ("F10", "upsertNodes: Tags als user.class, class-extends, class-properties, Werte", lambda f: mcp_tags(ls, f, run)),
        ("F11", "datetime-Werte und feste Auswahlwerte", lambda f: datetime_and_choices(ls, f, run)),
    ]:
        if only and key not in only:
            continue
        f = Finding(key, q)
        findings.append(f)
        try:
            fn(f)
        except Exception:
            f.note("Abbruch: " + _short(traceback.format_exc(), 600))
        report(out / "report.md", findings, run, graph)  # nach jedem Schritt, falls etwas hängt
    print(out / "report.md")


if __name__ == "__main__":
    main()
