"""Writing tools (phase 2). All follow writer.Writer: validate, write, read back."""

from __future__ import annotations

import datetime as dt
from typing import Any

from ..graph import DELETED_AT
from ..values import InvalidValue
from ..writer import POSITIONS, Writer, outline_ancestors
from .base import UUID_SCHEMA, Context, Tool

VALUE_HELP = (
    "Properties: {title or ident: value}. Text, number, true/false, ISO date 'YYYY-MM-DD' (date), "
    "ISO date-time (datetime), a choice title, a node UUID, title or ID like N-007 (node), a list for 'many' "
    "properties, null to remove.")

BLOCK_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "tags": {"type": "array", "items": {"type": "string"}},
        "properties": {"type": "object"},
        "children": {"type": "array", "items": {"$ref": "#/$defs/block"}},
    },
    "required": ["title"],
}

COMMON = {
    "dry_run": {"type": "boolean", "default": False,
                "description": "Validate and show the plan without writing"},
    "create_missing_links": {"type": "boolean", "default": False,
                             "description": "Allow [[links]] to pages that do not exist (Logseq creates them empty)"},
}


def _schema(props: dict, required: list[str], blocks: bool = False) -> dict:
    s: dict = {"type": "object", "properties": {**props, **COMMON}, "required": required}
    if blocks:
        s["$defs"] = {"block": BLOCK_SCHEMA}
    return s


def _exists(title: str, e: dict) -> InvalidValue:
    where = " in Logseq's recycle bin; restore or delete it there first" if DELETED_AT in e else ""
    return InvalidValue(f"'{title}' already exists ({e['uuid']}){where}; titles are unique across pages, tags and properties")


def _plan(blocks) -> list[dict]:
    return [{"title": b.title, "tags": [t.title for t in b.tags],
             "properties": {p.title: (e.expected if e else None) for p, e in b.props},
             **({"children": _plan(b.children)} if b.children else {})} for b in blocks]


class CreatePage(Tool):
    name = "create_page"
    writes = True
    description = "Create a page, optionally with tags, properties and an initial outline. " + VALUE_HELP
    input_schema = _schema({
        "title": {"type": "string", "minLength": 1},
        "tags": {"type": "array", "items": {"type": "string"}},
        "properties": {"type": "object"},
        "blocks": {"type": "array", "items": {"$ref": "#/$defs/block"}},
    }, ["title"], blocks=True)

    def run(self, ctx: Context, args: dict) -> Any:
        w = Writer(ctx, args.get("dry_run", False), args.get("create_missing_links", False))
        title = args["title"].strip()
        existing = ctx.graph.entities_by_title(title)
        if existing:
            raise _exists(title, existing[0])
        tags = w.tags(args.get("tags"))
        props = w.props(args.get("properties"))
        blocks = [w.block(b) for b in args.get("blocks") or []]
        ctx.access.check_new(title, {t.id for t in tags})
        if w.dry_run:
            return {"dry_run": True, "page": {"title": title, "tags": [t.title for t in tags],
                                              "properties": {p.title: e.expected if e else None for p, e in props}},
                    "blocks": _plan(blocks)}
        page = w.create_page(title)
        w.apply_tags(page["uuid"], tags)
        w.apply_props(page["uuid"], props)
        if blocks:
            w.insert_tree(page, "last_child", blocks)
        w.cleanup_new_pages({b.uuid for b in blocks})
        w.verify_node(page["uuid"], title, tags, (), props)
        created = w.verify_tree(blocks)
        return w.result(page={"uuid": page["uuid"], "title": title}, blocks=created,
                        changed=[page["uuid"], *(b["uuid"] for b in created)])


class InsertBlocks(Tool):
    name = "insert_blocks"
    writes = True
    description = (
        "Insert an outline of blocks relative to a page or block: as last or first child, or before/after "
        "a block. Each block may carry tags, properties and children. " + VALUE_HELP)
    input_schema = _schema({
        "target": UUID_SCHEMA,
        "position": {"enum": POSITIONS, "default": "last_child"},
        "blocks": {"type": "array", "minItems": 1, "items": {"$ref": "#/$defs/block"}},
    }, ["target", "blocks"], blocks=True)

    def run(self, ctx: Context, args: dict) -> Any:
        w = Writer(ctx, args.get("dry_run", False), args.get("create_missing_links", False))
        target = ctx.graph.require(args["target"])
        w.writable(target)
        position = args.get("position", "last_child")
        if position in ("before", "after") and "page" not in target:
            raise InvalidValue(f"'{position}' needs a block as target, not a page")
        blocks = [w.block(b) for b in args["blocks"]]
        if w.dry_run:
            return {"dry_run": True, "target": target["uuid"], "position": position, "blocks": _plan(blocks)}
        w.insert_tree(target, position, blocks)
        created = w.verify_tree(blocks)
        return w.result(blocks=created, changed=[b["uuid"] for b in created])


class UpdateBlock(Tool):
    name = "update_block"
    writes = True
    description = "Replace the text of a block (pages are renamed with rename_page)."
    input_schema = _schema({"uuid": UUID_SCHEMA, "title": {"type": "string"}}, ["uuid", "title"])

    def run(self, ctx: Context, args: dict) -> Any:
        w = Writer(ctx, args.get("dry_run", False), args.get("create_missing_links", False))
        e = ctx.graph.require(args["uuid"])
        if "page" not in e:
            raise InvalidValue("this is a page; use rename_page")
        w.writable(e)
        w.check_links(args["title"])
        if w.dry_run:
            return {"dry_run": True, "uuid": e["uuid"], "old": e.get("title"), "new": args["title"]}
        w._call(f"update {e['uuid']}", "logseq.Editor.updateBlock", e["uuid"], args["title"])
        w.verify_node(e["uuid"], args["title"])
        return w.result(uuid=e["uuid"], changed=[e["uuid"]])


class MoveBlocks(Tool):
    name = "move_blocks"
    writes = True
    description = (
        "Move blocks (with their children) relative to a page or block, keeping their order: as last or "
        "first child, or before/after a block.")
    input_schema = {"type": "object", "properties": {
        "uuids": {"type": "array", "minItems": 1, "items": UUID_SCHEMA},
        "target": UUID_SCHEMA,
        "position": {"enum": POSITIONS, "default": "last_child"},
        "dry_run": COMMON["dry_run"]},
        "required": ["uuids", "target"]}

    def run(self, ctx: Context, args: dict) -> Any:
        w = Writer(ctx, args.get("dry_run", False))
        g = ctx.graph
        target = g.require(args["target"])
        w.writable(target)
        position = args.get("position", "last_child")
        moving = [g.require(u) for u in args["uuids"]]
        ids = {m["id"] for m in moving}
        for m in moving:
            if "page" not in m:
                raise InvalidValue(f"{m['uuid']} is a page; only blocks can be moved")
            w.writable(m)
        if target["id"] in ids or ids & outline_ancestors(g, target):
            raise InvalidValue("cannot move a block into itself or below itself")
        if position in ("before", "after") and "page" not in target:
            raise InvalidValue(f"'{position}' needs a block as target, not a page")
        if w.dry_run:
            return {"dry_run": True, "move": [m["uuid"] for m in moving], "target": target["uuid"], "position": position}

        def move(src: str, anchor: str, opts: dict) -> None:
            w._call(f"move {src}", "logseq.Editor.moveBlock", src, anchor, opts)

        # Only the first block is placed relative to the target; every further
        # block goes right after its predecessor. Repeated "before: true" moves
        # do not stack in order (docs/API-BEFUNDE.md).
        first, rest = moving[0]["uuid"], [m["uuid"] for m in moving[1:]]
        if position == "before":
            move(first, target["uuid"], {"before": True})
        elif position == "after":
            move(first, target["uuid"], {})
        else:
            kids = [k for k in g.children(target) if k["id"] not in ids]
            if "page" not in target:  # a page: anchor on its top-level blocks
                if not kids:
                    raise InvalidValue("the page is empty; insert a block first, then move below it")
                anchor = kids[0] if position == "first_child" else kids[-1]
                move(first, anchor["uuid"], {"before": True} if position == "first_child" else {})
            elif kids and position == "last_child":
                move(first, kids[-1]["uuid"], {})
            else:
                move(first, target["uuid"], {"children": True})  # becomes the first child
        prev = first
        for u in rest:
            move(u, prev, {})
            prev = u
        # read back: the moved blocks sit together, in order, at the place asked for
        moved = [g.require(m["uuid"]) for m in moving]
        parent_ids = {(m.get("parent") or {}).get("id") for m in moved}
        if len(parent_ids) != 1:
            w.problems.append("the moved blocks did not end up under one parent")
        else:
            parent = g.pull_ids(parent_ids).get(next(iter(parent_ids)))
            order = [k["uuid"] for k in g.children(parent)] if parent else []
            want = [m["uuid"] for m in moving]
            if not any(order[i:i + len(want)] == want for i in range(len(order))):
                w.problems.append(f"order after the move: {order}")
        return w.result(moved=[m["uuid"] for m in moving], changed=[m["uuid"] for m in moving])


class RenamePage(Tool):
    name = "rename_page"
    writes = True
    description = "Rename a page; links to it follow."
    input_schema = {"type": "object", "properties": {"uuid": UUID_SCHEMA, "title": {"type": "string", "minLength": 1},
                                                     "dry_run": COMMON["dry_run"]},
                    "required": ["uuid", "title"]}

    def run(self, ctx: Context, args: dict) -> Any:
        w = Writer(ctx, args.get("dry_run", False))
        e = ctx.graph.require(args["uuid"])
        if ctx.graph.kind(e) != "page":
            raise InvalidValue(f"this is a {ctx.graph.kind(e)}; only pages can be renamed here")
        w.writable(e)
        new = args["title"].strip()
        clash = [x for x in ctx.graph.entities_by_title(new) if x["id"] != e["id"]]
        if clash:
            raise _exists(new, clash[0])
        if w.dry_run:
            return {"dry_run": True, "uuid": e["uuid"], "old": e.get("title"), "new": new}
        w._call(f"rename {e['uuid']}", "logseq.Editor.renamePage", e["uuid"], new)
        w.verify_node(e["uuid"], new)
        return w.result(uuid=e["uuid"], changed=[e["uuid"]])


class DeleteNodes(Tool):
    name = "delete_nodes"
    writes = True
    destructive = True
    description = (
        "Delete blocks (with their children) or pages. Pages go to Logseq's recycle bin (restorable there, "
        "hidden from all other tools); blocks are removed. Without confirm: true it only reports what would "
        "be lost (children, backlinks). Tags and properties are not deleted here.")
    input_schema = {"type": "object", "properties": {
        "uuids": {"type": "array", "minItems": 1, "items": UUID_SCHEMA},
        "confirm": {"type": "boolean", "default": False}},
        "required": ["uuids"]}

    def run(self, ctx: Context, args: dict) -> Any:
        g = ctx.graph
        w = Writer(ctx)
        report = []
        nodes = [g.require(u) for u in args["uuids"]]
        for e in nodes:
            kind = g.kind(e)
            if kind in ("tag", "property"):
                raise InvalidValue(f"{e['uuid']} is a {kind}; schema changes are made in the Logseq UI")
            w.writable(e)
            backlinks = ctx.client.q("[:find (count ?b) :in $ ?t :where [?b :block/refs ?t]]", str(e["id"]))
            report.append({"uuid": e["uuid"], "kind": kind, "title": e.get("title"),
                           "children": len(g.children(e)),
                           "backlinks": backlinks[0][0] if backlinks else 0})
        if not args.get("confirm"):
            return {"confirm_needed": True, "would_delete": report}
        for e in nodes:
            if "page" in e:
                w._call(f"delete block {e['uuid']}", "logseq.Editor.removeBlock", e["uuid"])
            else:
                w._call(f"delete page {e['uuid']}", "logseq.Editor.deletePage", e["title"])
        for e in nodes:
            if "page" in e and g.entity_by_uuid(e["uuid"]) is not None:
                w.problems.append(f"{e['uuid']} still exists")
            elif "page" not in e:
                gone = g.entity_by_uuid(e["uuid"])
                if gone is not None and DELETED_AT not in gone:
                    w.problems.append(f"page {e['uuid']} still exists")
        return w.result(deleted=report, changed=[e["uuid"] for e in nodes])


class CreateObjects(Tool):
    name = "create_objects"
    writes = True
    description = (
        "Create objects: nodes with one or more tags and their properties, either as pages (as_page) or as "
        "blocks placed relative to a target. Reports class properties left empty. " + VALUE_HELP)
    input_schema = _schema({
        "objects": {"type": "array", "minItems": 1, "items": {"type": "object", "properties": {
            "tags": {"type": "array", "minItems": 1, "items": {"type": "string"}},
            "title": {"type": "string", "minLength": 1},
            "properties": {"type": "object"},
            "as_page": {"type": "boolean", "default": False},
            "target": UUID_SCHEMA,
            "position": {"enum": POSITIONS, "default": "last_child"},
            "children": {"type": "array", "items": {"$ref": "#/$defs/block"}}},
            "required": ["tags", "title"]}},
    }, ["objects"], blocks=True)

    def run(self, ctx: Context, args: dict) -> Any:
        w = Writer(ctx, args.get("dry_run", False), args.get("create_missing_links", False))
        schema = ctx.schema.get()
        plans = []
        for spec in args["objects"]:
            tags = w.tags(spec["tags"])
            block = w.block({"title": spec["title"], "properties": spec.get("properties"),
                             "children": spec.get("children")})
            block.tags = tags
            given = {p.ident for p, _ in block.props}
            missing = sorted({p.title for t in tags for p in schema.class_properties(t.id) if p.ident not in given})
            if spec.get("as_page"):
                existing = ctx.graph.entities_by_title(spec["title"])
                if existing:
                    raise _exists(spec["title"], existing[0])
                ctx.access.check_new(spec["title"], {t.id for t in tags})
                target = None
            else:
                if not spec.get("target"):
                    raise InvalidValue(f"'{spec['title']}': give a target (page or block UUID) or set as_page")
                target = ctx.graph.require(spec["target"])
                w.writable(target)
                pos = spec.get("position", "last_child")
                if pos in ("before", "after") and "page" not in target:
                    raise InvalidValue(f"'{pos}' needs a block as target, not a page")
            plans.append((spec, block, target, missing))
        if w.dry_run:
            return {"dry_run": True, "objects": [
                {**_plan([b])[0], "as_page": bool(s.get("as_page")), "target": t["uuid"] if t else None,
                 "missing_class_properties": m} for s, b, t, m in plans]}
        created = []
        for spec, block, target, missing in plans:
            if target is None:
                page = w.create_page(block.title)
                block.uuid = page["uuid"]
                w.apply_tags(page["uuid"], block.tags)
                w.apply_props(page["uuid"], block.props)
                if block.children:
                    w.insert_tree(page, "last_child", block.children)
            else:
                w.insert_tree(target, spec.get("position", "last_child"), [block])
            node = w.verify_node(block.uuid, block.title, block.tags, (), block.props)
            w.verify_tree(block.children)
            created.append({"uuid": block.uuid, "title": (node or {}).get("title", block.title),
                            "missing_class_properties": missing})
        w.cleanup_new_pages(set())
        return w.result(objects=created, changed=[c["uuid"] for c in created])


class UpdateObjects(Tool):
    name = "update_objects"
    writes = True
    description = (
        "Change titles and properties of nodes (objects, blocks or pages): set values or remove them "
        "with null. Several nodes per call. " + VALUE_HELP)
    input_schema = _schema({
        "updates": {"type": "array", "minItems": 1, "items": {"type": "object", "properties": {
            "uuid": UUID_SCHEMA, "title": {"type": "string", "minLength": 1}, "properties": {"type": "object"}},
            "required": ["uuid"]}},
    }, ["updates"])

    def run(self, ctx: Context, args: dict) -> Any:
        w = Writer(ctx, args.get("dry_run", False), args.get("create_missing_links", False))
        plans = []
        for u in args["updates"]:
            e = ctx.graph.require(u["uuid"])
            w.writable(e)
            title = u.get("title")
            if title is not None:
                if "page" not in e:
                    clash = [x for x in ctx.graph.entities_by_title(title) if x["id"] != e["id"]]
                    if clash:
                        raise _exists(title, clash[0])
                else:
                    w.check_links(title)
            plans.append((e, title, w.props(u.get("properties"))))
        if w.dry_run:
            return {"dry_run": True, "updates": [
                {"uuid": e["uuid"], "title": t, "properties": {p.title: x.expected if x else None for p, x in props}}
                for e, t, props in plans]}
        for e, title, props in plans:
            if title is not None and title != e.get("title"):
                if "page" in e:
                    w._call(f"update {e['uuid']}", "logseq.Editor.updateBlock", e["uuid"], title)
                else:
                    w._call(f"rename {e['uuid']}", "logseq.Editor.renamePage", e["uuid"], title)
            w.apply_props(e["uuid"], props)
            w.verify_node(e["uuid"], title, (), (), props)
        return w.result(updated=[e["uuid"] for e, _, _ in plans], changed=[e["uuid"] for e, _, _ in plans])


class _TagTool(Tool):
    writes = True
    input_schema = {"type": "object", "properties": {
        "uuids": {"type": "array", "minItems": 1, "items": UUID_SCHEMA},
        "tags": {"type": "array", "minItems": 1, "items": {"type": "string"}}},
        "required": ["uuids", "tags"]}
    add = True

    def run(self, ctx: Context, args: dict) -> Any:
        w = Writer(ctx)
        tags = w.tags(args["tags"])
        nodes = [ctx.graph.require(u) for u in args["uuids"]]
        for e in nodes:
            w.writable(e)
        if self.add:
            ctx.access.check_tags_visible({t.id for t in tags})
        for e in nodes:
            if self.add:
                w.apply_tags(e["uuid"], tags)
                w.verify_node(e["uuid"], tags=tags)
            else:
                w.apply_tags(e["uuid"], [], tags)
                w.verify_node(e["uuid"], untagged=tags)
        return w.result(nodes=[e["uuid"] for e in nodes], changed=[e["uuid"] for e in nodes])


class TagNodes(_TagTool):
    name = "tag_nodes"
    description = "Add tags to nodes (blocks or pages), making them objects of those tags."


class UntagNodes(_TagTool):
    name = "untag_nodes"
    description = "Remove tags from nodes; their property values stay."
    add = False


class AddToJournal(Tool):
    name = "add_to_journal"
    writes = True
    description = "Append blocks to the journal of a date (default today); creates the journal if needed. " + VALUE_HELP
    input_schema = _schema({
        "date": {"type": "string", "description": "YYYY-MM-DD; default today"},
        "blocks": {"type": "array", "minItems": 1, "items": {"$ref": "#/$defs/block"}},
    }, ["blocks"], blocks=True)

    def run(self, ctx: Context, args: dict) -> Any:
        w = Writer(ctx, args.get("dry_run", False), args.get("create_missing_links", False))
        day = args.get("date") or dt.date.today().isoformat()
        dt.date.fromisoformat(day)
        blocks = [w.block(b) for b in args["blocks"]]
        journal = ctx.graph.journal(day)
        if journal is not None:
            w.writable(journal)
        if w.dry_run:
            return {"dry_run": True, "date": day, "journal_exists": journal is not None, "blocks": _plan(blocks)}
        if journal is None:
            ctx.access.check_new(day, set())
            journal = ctx.graph.journal(day, create=True)
            w.done.append(f"create journal {day}")
            if journal is None:
                raise InvalidValue(f"could not create the journal for {day}")
        w.insert_tree(journal, "last_child", blocks)
        created = w.verify_tree(blocks)
        return w.result(journal=journal["uuid"], blocks=created, changed=[b["uuid"] for b in created])


WRITE_TOOLS = [CreatePage(), InsertBlocks(), UpdateBlock(), MoveBlocks(), RenamePage(), DeleteNodes(),
               CreateObjects(), UpdateObjects(), TagNodes(), UntagNodes(), AddToJournal()]
