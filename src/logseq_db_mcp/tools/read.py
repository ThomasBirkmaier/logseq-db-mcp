"""Reading tools (phase 1)."""

from __future__ import annotations

import datetime as dt
from typing import Any

from ..client import edn_ints, edn_str, edn_uuid
from ..graph import is_value_block
from .base import UUID_SCHEMA, Context, Tool


class GetSchema(Tool):
    name = "get_schema"
    description = (
        "The graph's tags (classes) with inheritance and class properties, and its properties with "
        "type, cardinality, allowed tags and choices. Read this before creating or filtering objects.")
    input_schema = {"type": "object", "properties": {
        "include_builtin": {"type": "boolean", "default": False,
                            "description": "Also list Logseq's built-in tags and properties"}}}

    def run(self, ctx: Context, args: dict) -> Any:
        ctx.schema.invalidate()
        s = ctx.schema.get()
        builtin = args.get("include_builtin", False)
        counts = {t: n for t, n in ctx.client.q("[:find ?t (count ?b) :where [?b :block/tags ?t]]")}

        def title(pid: int) -> str:
            return s.properties_by_id[pid].title if pid in s.properties_by_id else str(pid)

        tags = []
        for t in sorted(s.tags.values(), key=lambda t: t.title.lower()):
            if t.builtin and not builtin:
                continue
            own = [title(p) for p in t.properties]
            inherited = [p.title for p in s.class_properties(t.id) if p.title not in own]
            tags.append({
                "title": t.title, "uuid": t.uuid, "ident": t.ident,
                "extends": [s.tags[e].title for e in t.extends if e in s.tags and s.tags[e].ident != ":logseq.class/Root"],
                "properties": own, "inherited_properties": inherited, "objects": counts.get(t.id, 0)})
        props = []
        for p in sorted(s.properties.values(), key=lambda p: p.title.lower()):
            if p.builtin and not builtin:
                continue
            if p.hidden:
                continue
            entry = {"title": p.title, "uuid": p.uuid, "ident": p.ident, "type": p.type,
                     "cardinality": "many" if p.many else "one"}
            if p.classes:
                entry["classes"] = [s.tags[c].title for c in p.classes if c in s.tags]
            if p.choices:
                entry["choices"] = list(p.choices.values())
            props.append(entry)
        return {"tags": tags, "properties": props}


class Resolve(Tool):
    name = "resolve"
    description = (
        "Find nodes by title: pages, journals, tags and properties (and blocks with kinds=['block']). "
        "Exact matches (ignoring case) come first, then titles containing the text. Use the UUIDs it "
        "returns in all other tools; titles can be ambiguous.")
    input_schema = {"type": "object", "properties": {
        "title": {"type": "string", "minLength": 1},
        "kinds": {"type": "array", "items": {"enum": ["page", "journal", "tag", "property", "block"]}},
        "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 10}},
        "required": ["title"]}

    def run(self, ctx: Context, args: dict) -> Any:
        wanted = args["title"].strip().lower()
        kinds = set(args.get("kinds") or ["page", "journal", "tag", "property"])
        limit = args.get("limit", 10)
        exact, partial = [], []
        for eid, title in ctx.graph.titled_entities():
            if not isinstance(title, str):
                continue
            t = title.lower()
            if t == wanted:
                exact.append(eid)
            elif wanted in t:
                partial.append(eid)
        if "block" in kinds:
            rows = ctx.client.q("[:find ?e :in $ ?t :where [?e :block/title ?t] [?e :block/page]]",
                                edn_str(args["title"]))
            exact.extend(r[0] for r in rows)
        partial.sort(key=lambda i: i)
        ents = ctx.graph.pull_ids(exact + partial)
        ordered = [ents[i] for i in exact + partial if i in ents and not is_value_block(ents[i])]
        nodes = [e for e in ordered if ctx.graph.kind(e) in kinds][:limit]
        matches = ctx.graph.serialize(nodes)
        return {"matches": matches, "exact": sum(1 for m in matches if m["title"].lower() == wanted)}


class GetNode(Tool):
    name = "get_node"
    description = (
        "A page, journal, tag, property or block by UUID: its tags, properties and outline children "
        "down to `depth` levels (0 = the node only). Property values come typed: text, numbers, ISO "
        "dates, and node references as {uuid, title}.")
    input_schema = {"type": "object", "properties": {
        "uuid": UUID_SCHEMA,
        "depth": {"type": "integer", "minimum": 0, "maximum": 20, "default": 2},
        "include_idents": {"type": "boolean", "default": False,
                           "description": "Show property and tag idents (needed for query)"}},
        "required": ["uuid"]}

    def run(self, ctx: Context, args: dict) -> Any:
        e = ctx.graph.require(args["uuid"])
        ctx.access.check_read(e)
        out = ctx.graph.serialize([e], depth=args.get("depth", 2), include_idents=args.get("include_idents", False),
                                  include_recycled=True)
        return out[0] if out else None


# --- find_objects ---------------------------------------------------------------

OPS = ["eq", "ne", "contains", "empty", "not_empty", "gt", "gte", "lt", "lte"]


def _norm(x: Any) -> Any:
    return x.lower() if isinstance(x, str) else x


def _item_eq(item: Any, wanted: Any) -> bool:
    if isinstance(item, dict):
        return _norm(wanted) in (item.get("uuid"), _norm(item.get("title")))
    return _norm(item) == _norm(wanted)


def _item_contains(item: Any, wanted: Any) -> bool:
    if isinstance(item, dict):
        item = item.get("title") or ""
    if isinstance(item, str) and isinstance(wanted, str):
        return wanted.lower() in item.lower()
    return item == wanted


def matches(value: Any, op: str, wanted: Any) -> bool:
    items = value if isinstance(value, list) else ([] if value is None else [value])
    if op == "empty":
        return not items or items == [""]
    if op == "not_empty":
        return bool(items) and items != [""]
    if op == "eq":
        return any(_item_eq(i, wanted) for i in items)
    if op == "ne":
        return not any(_item_eq(i, wanted) for i in items)
    if op == "contains":
        return any(_item_contains(i, wanted) for i in items)
    for i in items:
        i = i.get("title") if isinstance(i, dict) else i
        try:
            if (op == "gt" and i > wanted) or (op == "gte" and i >= wanted) or \
               (op == "lt" and i < wanted) or (op == "lte" and i <= wanted):
                return True
        except TypeError:
            continue
    return False


class FindObjects(Tool):
    name = "find_objects"
    description = (
        "Objects of a tag (by title, ident or UUID), by default including objects of every tag that "
        "extends it. Filter by property values (`where`, all conditions must hold), sort and page. "
        "`property` is a property title or ident, or 'title'. Node references match by UUID or title.")
    input_schema = {"type": "object", "properties": {
        "tag": {"type": "string", "minLength": 1},
        "include_subtags": {"type": "boolean", "default": True},
        "where": {"type": "array", "items": {"type": "object", "properties": {
            "property": {"type": "string"}, "op": {"enum": OPS}, "value": {}},
            "required": ["property", "op"]}},
        "sort_by": {"type": "string", "default": "title",
                    "description": "'title', 'updated' or a property title"},
        "descending": {"type": "boolean", "default": False},
        "limit": {"type": "integer", "minimum": 1, "maximum": 500, "default": 50},
        "offset": {"type": "integer", "minimum": 0, "default": 0}},
        "required": ["tag"]}

    def run(self, ctx: Context, args: dict) -> Any:
        tag = ctx.schema.tag(args["tag"])
        schema = ctx.schema.get()
        tag_ids = schema.subtags(tag.id) if args.get("include_subtags", True) else {tag.id}
        rows = ctx.client.q("[:find ?b :in $ [?t ...] :where [?b :block/tags ?t]]", edn_ints(tag_ids))
        ents = ctx.graph.pull_ids(r[0] for r in rows)
        objects = ctx.graph.serialize(list(ents.values()))

        def field(o: dict, name: str) -> Any:
            if name == "title":
                return o.get("title")
            if name == "updated":
                return o.get("updated")
            return (o.get("properties") or {}).get(ctx.schema.property(name).title)

        for cond in args.get("where") or []:
            objects = [o for o in objects if matches(field(o, cond["property"]), cond["op"], cond.get("value"))]

        sort_by = args.get("sort_by", "title")

        def sort_value(o: dict):
            v = field(o, sort_by)
            v = v[0] if isinstance(v, list) and v else v
            v = v.get("title") if isinstance(v, dict) else v
            return None if v in (None, [], "") else _norm(v)

        # empty values last, in either direction
        present = [o for o in objects if sort_value(o) is not None]
        absent = [o for o in objects if sort_value(o) is None]
        try:
            present.sort(key=sort_value, reverse=args.get("descending", False))
        except TypeError:  # mixed types: compare as text
            present.sort(key=lambda o: str(sort_value(o)), reverse=args.get("descending", False))
        objects = present + absent
        offset, limit = args.get("offset", 0), args.get("limit", 50)
        return {"tag": tag.title, "tags_searched": sorted(schema.tags[t].title for t in tag_ids),
                "total": len(objects), "objects": objects[offset:offset + limit]}


class GetBacklinks(Tool):
    name = "get_backlinks"
    description = (
        "Nodes that refer to a node: links in their text, property values pointing to it, and (for a "
        "tag) objects carrying it.")
    input_schema = {"type": "object", "properties": {
        "uuid": UUID_SCHEMA, "limit": {"type": "integer", "minimum": 1, "maximum": 500, "default": 100}},
        "required": ["uuid"]}

    def run(self, ctx: Context, args: dict) -> Any:
        target = ctx.graph.require(args["uuid"])
        ctx.access.check_read(target)
        rows = ctx.client.q("[:find ?b :in $ ?u :where [?t :block/uuid ?u] [?b :block/refs ?t]]",
                            edn_uuid(args["uuid"]))
        ents = ctx.graph.pull_ids(r[0] for r in rows)
        holders = {e["parent"]["id"] for e in ents.values() if is_value_block(e) and e.get("parent")}
        ents.update(ctx.graph.pull_ids(holders - set(ents)))
        nodes = [e for e in ents.values() if not is_value_block(e) and e["id"] != target["id"]]
        nodes.sort(key=lambda e: e.get("updated-at", 0), reverse=True)
        out = ctx.graph.serialize(nodes)
        return {"total": len(out), "backlinks": out[:args.get("limit", 100)]}


class GetJournal(Tool):
    name = "get_journal"
    description = "The journal page of a date (default: today) with its outline."
    input_schema = {"type": "object", "properties": {
        "date": {"type": "string", "description": "YYYY-MM-DD; default today"},
        "depth": {"type": "integer", "minimum": 0, "maximum": 20, "default": 5}}}

    def run(self, ctx: Context, args: dict) -> Any:
        day = args.get("date") or dt.date.today().isoformat()
        dt.date.fromisoformat(day)
        j = ctx.graph.journal(day)
        if j is None:
            return {"date": day, "exists": False}
        ctx.access.check_read(j)
        return ctx.graph.serialize([j], depth=args.get("depth", 5))[0]


class Search(Tool):
    name = "search"
    description = (
        "Full-text search over block and page titles, ignoring case. A hit inside a property value "
        "returns the block carrying that value.")
    input_schema = {"type": "object", "properties": {
        "text": {"type": "string", "minLength": 1},
        "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20}},
        "required": ["text"]}

    def run(self, ctx: Context, args: dict) -> Any:
        res = ctx.client.call("logseq.App.search", args["text"]) or {}
        ids: list[int] = []
        for b in res.get("blocks") or []:
            if isinstance(b, dict) and isinstance(b.get("id"), int):
                ids.append(b["id"])
        ents = ctx.graph.pull_ids(ids)
        holders = {e["parent"]["id"] for e in ents.values() if is_value_block(e) and e.get("parent")}
        ents.update(ctx.graph.pull_ids(holders - set(ents)))
        order: list[int] = []
        for i in ids:
            e = ents.get(i)
            if e is None:
                continue
            if is_value_block(e):
                i = e["parent"]["id"]
            if i not in order and i in ents:
                order.append(i)
        for p in res.get("pages") or []:
            title = p if isinstance(p, str) else (p or {}).get("title")
            for e in ctx.graph.entities_by_title(title) if title else []:
                ents[e["id"]] = e
                if e["id"] not in order:
                    order.append(e["id"])
        out = ctx.graph.serialize([ents[i] for i in order])
        return {"total": len(out), "results": out[:args.get("limit", 20)]}


class Query(Tool):
    name = "query"
    description = (
        "Run a read-only Datascript query against the graph. Inputs are EDN strings, e.g. "
        "'#uuid \"…\"', '20301015' or '[1 2 3]'. Attributes: :block/title, :block/tags, :block/page, "
        ":block/parent, :block/refs, :block/journal-day, :db/ident, and property idents from get_schema "
        "(get_node with include_idents shows them). pull [*] returns core attributes without namespace.")
    input_schema = {"type": "object", "properties": {
        "query": {"type": "string", "minLength": 1},
        "inputs": {"type": "array", "items": {"type": "string"}, "default": []},
        "limit": {"type": "integer", "minimum": 1, "maximum": 2000, "default": 200}},
        "required": ["query"]}

    def run(self, ctx: Context, args: dict) -> Any:
        if ctx.settings.hide_tags:
            raise PermissionError("query cannot apply LOGSEQ_HIDE_TAGS and is disabled while it is set")
        result = ctx.client.call("logseq.DB.datascriptQuery", args["query"], *args.get("inputs", []))
        if not isinstance(result, list):
            return {"result": result}
        limit = args.get("limit", 200)
        return {"total": len(result), "truncated": len(result) > limit, "rows": result[:limit]}


READ_TOOLS = [GetSchema(), Resolve(), GetNode(), FindObjects(), GetBacklinks(), GetJournal(), Search(), Query()]

