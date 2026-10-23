"""Shared write logic: validate everything first, then write, then read back.

Logseq's API has no transactions and often answers ``null`` even when nothing
happened (docs/API-BEFUNDE.md, F6/F8). So every write is

1. validated completely before the first call (tags, properties, links, access),
2. executed call by call, recording what was done (``PartialWrite`` reports it),
3. read back and compared (``verified`` / ``problems``).
"""

from __future__ import annotations

import uuid as uuidlib
from dataclasses import dataclass, field
from typing import Any

from . import refs, values
from .client import LogseqError, edn_int
from .schema import REF_TYPES, SHOWN_BUILTINS, Property, Tag
from .tools.base import Context
from .values import Encoded, InvalidValue

POSITIONS = ["last_child", "first_child", "before", "after"]


class PartialWrite(LogseqError):
    def __init__(self, message: str, done: list[str]):
        super().__init__(f"{message}. Already written before the failure: {done or 'nothing'}")
        self.done = done


@dataclass
class PreparedBlock:
    title: str
    tags: list[Tag]
    props: list[tuple[Property, Encoded | None]]
    children: list["PreparedBlock"] = field(default_factory=list)
    uuid: str = field(default_factory=lambda: str(uuidlib.uuid4()))


class Writer:
    def __init__(self, ctx: Context, dry_run: bool = False, create_missing_links: bool = False):
        self.ctx = ctx
        self.graph = ctx.graph
        self.client = ctx.client
        self.dry_run = dry_run
        self.create_missing_links = create_missing_links
        self.done: list[str] = []
        self.problems: list[str] = []
        self._titles: set[str] | None = None
        self.new_pages: list[str] = []

    # --- validation -----------------------------------------------------------

    def _known_titles(self) -> set[str]:
        if self._titles is None:
            self._titles = {t.lower() for _, t in self.graph.titled_entities() if isinstance(t, str)}
        return self._titles

    def check_links(self, text: str) -> None:
        if self.create_missing_links:
            return
        for link in refs.title_links(text):
            if link.lower() not in self._known_titles():
                raise InvalidValue(
                    f"[[{link}]] links to a page that does not exist; Logseq would create an empty one. "
                    "Create it first (create_page) or set create_missing_links: true.")

    def tags(self, names: list[str] | None) -> list[Tag]:
        out = []
        for n in names or []:
            t = self.ctx.schema.tag(n)
            if t.ident in (":logseq.class/Tag", ":logseq.class/Property", ":logseq.class/Page",
                           ":logseq.class/Journal"):
                raise InvalidValue(f"'{t.title}' says what kind a node is and cannot be set as a tag")
            out.append(t)
        return out

    def props(self, given: dict | None) -> list[tuple[Property, Encoded | None]]:
        out = []
        for key, value in (given or {}).items():
            p = self.ctx.schema.property(key)
            if p.builtin and p.ident not in SHOWN_BUILTINS:
                raise InvalidValue(f"'{p.title}' is a built-in property and cannot be set here")
            if value is None:
                out.append((p, None))
                continue
            try:
                out.append((p, values.encode(p, value, self.graph, create_journals=not self.dry_run)))
            except InvalidValue as e:
                if self.dry_run and p.type == "date" and "no journal page" in str(e):
                    out.append((p, Encoded(None, {}, values._iso_date(value))))  # created on the real run
                else:
                    raise
        return out

    def block(self, spec: dict) -> PreparedBlock:
        title = spec.get("title", "")
        if not isinstance(title, str):
            raise InvalidValue("block title must be text")
        self.check_links(title)
        return PreparedBlock(title=title, tags=self.tags(spec.get("tags")), props=self.props(spec.get("properties")),
                             children=[self.block(c) for c in spec.get("children") or []])

    def writable(self, e: dict) -> dict:
        page = self.graph.page_of(e)
        self.ctx.access.check_write(e, page)
        return page

    # --- execution ------------------------------------------------------------

    def _call(self, label: str, method: str, *args) -> Any:
        try:
            result = self.client.call(method, *args)
        except LogseqError as e:
            raise PartialWrite(f"{label} failed: {e}", self.done) from e
        self.done.append(label)
        return result

    def apply_tags(self, uuid: str, add: list[Tag], remove: list[Tag] = ()) -> None:
        for t in add:
            self._call(f"tag {uuid} #{t.title}", "logseq.Editor.addBlockTag", uuid, t.uuid)
        for t in remove:
            self._call(f"untag {uuid} #{t.title}", "logseq.Editor.removeBlockTag", uuid, t.uuid)

    def apply_props(self, uuid: str, props: list[tuple[Property, Encoded | None]]) -> None:
        for p, enc in props:
            stale = self._text_ref_blocks(uuid, p)
            if enc is None:
                self._call(f"remove {p.title} on {uuid}", "logseq.Editor.removeBlockProperty", uuid, p.ident)
                self._drop_orphans(p, stale)
                continue
            if enc.value is None and p.type == "date":  # journal planned in a dry run
                enc = values.encode(p, enc.expected, self.graph, create_journals=True)
            if p.many:
                # set, not add: clear the old values first
                try:
                    self.client.call("logseq.Editor.removeBlockProperty", uuid, p.ident)
                except LogseqError:
                    pass
            self._call(f"set {p.title} on {uuid}", "logseq.Editor.upsertBlockProperty",
                       uuid, p.ident, enc.value, enc.opts)
            self._drop_orphans(p, stale)

    def _text_ref_blocks(self, uuid: str, p: Property) -> list[dict]:
        """Value blocks of ``p`` on ``uuid`` that hold a reference as text (API-BEFUNDE I10)."""
        if p.type not in REF_TYPES or self.dry_run:
            return []
        e = self.graph.entity_by_uuid(uuid) or {}
        blocks = self.graph.pull_ids(values.referenced_ids(e.get(p.ident))).values()
        return [b for b in blocks if values.text_ref_uuid(p, b)]

    def _drop_orphans(self, p: Property, blocks: list[dict]) -> None:
        """Replacing such a value leaves its block behind; remove it once nothing uses it."""
        for b in blocks:
            if self.client.q(f"[:find ?h :in $ ?v :where [?h {p.ident} ?v]]", edn_int(b["id"])):
                continue
            self._call(f"remove orphaned value block {b['uuid']}", "logseq.Editor.removeBlock", b["uuid"])

    def _insert_one(self, target: dict, position: str, block: PreparedBlock) -> None:
        """Insert one block (without children) relative to ``target``."""
        is_page = "page" not in target
        opts: dict = {"customUUID": block.uuid}
        anchor = target["uuid"]
        if position in ("before", "after"):
            if is_page:
                raise InvalidValue(f"'{position}' needs a block as target, not a page")
            opts.update(sibling=True, before=position == "before")
        else:
            kids = self.graph.children(target)
            if kids and position == "first_child":
                anchor = kids[0]["uuid"]
                opts.update(sibling=True, before=True)
            elif kids:
                anchor = kids[-1]["uuid"]
                opts.update(sibling=True)
            else:
                opts.update(sibling=False)
                if not is_page:
                    opts["end"] = True
        created = self._call(f"insert '{block.title[:40]}' ({block.uuid})", "logseq.Editor.insertBlock",
                             anchor, block.title, opts)
        if not isinstance(created, dict) or created.get("uuid") != block.uuid:
            raise PartialWrite(f"insertBlock did not create block {block.uuid} (answer: {created!r})", self.done)

    def insert_tree(self, target: dict, position: str, blocks: list[PreparedBlock]) -> None:
        """Insert ``blocks`` in order with their children, tags and properties."""
        prev: PreparedBlock | None = None
        for b in blocks:
            if prev is None:
                self._insert_one(target, position, b)
            else:
                # a fresh sibling: only its uuid is needed as anchor
                self._insert_one({"uuid": prev.uuid, "page": None}, "after", b)
            self.apply_tags(b.uuid, b.tags)
            self.apply_props(b.uuid, b.props)
            if b.children:
                self.insert_tree(self.graph.require(b.uuid), "last_child", b.children)
            prev = b

    def create_page(self, title: str) -> dict:
        page = self._call(f"create page '{title}'", "logseq.Editor.createPage", title, {}, {"createFirstBlock": False})
        if not isinstance(page, dict) or not page.get("uuid"):
            raise PartialWrite(f"createPage '{title}' returned {page!r}", self.done)
        self.new_pages.append(page["uuid"])
        self._titles = None
        return self.graph.require(page["uuid"])

    def cleanup_new_pages(self, keep: set[str]) -> None:
        """createPage adds an empty block even with createFirstBlock false; remove it."""
        for puuid in self.new_pages:
            page = self.graph.entity_by_uuid(puuid)
            if page is None:
                continue
            blocks = self.graph.page_blocks(page["id"])
            parents = {(b.get("parent") or {}).get("id") for b in blocks}
            for b in blocks:
                if b["uuid"] in keep or (b.get("title") or "") != "" or b["id"] in parents:
                    continue
                if any(k.startswith(":user.") or k.startswith(":plugin.") for k in b):
                    continue
                try:
                    self.client.call("logseq.Editor.removeBlock", b["uuid"])
                except LogseqError:
                    pass

    # --- read-back ------------------------------------------------------------

    def verify_node(self, uuid: str, title: str | None = None, tags: list[Tag] = (),
                    untagged: list[Tag] = (), props: list[tuple[Property, Encoded | None]] = ()) -> dict | None:
        e = self.graph.entity_by_uuid(uuid)
        if e is None:
            self.problems.append(f"{uuid}: not found after writing")
            return None
        node = self.graph.serialize([e])[0]
        if title is not None and _norm_title(node.get("title")) != _norm_title(title):
            self.problems.append(f"{uuid}: title is {node.get('title')!r}, expected {title!r}")
        have_tags = {t["uuid"] for t in node.get("tags") or []}
        for t in tags:
            if t.uuid not in have_tags:
                self.problems.append(f"{uuid}: tag {t.title} missing")
        for t in untagged:
            if t.uuid in have_tags:
                self.problems.append(f"{uuid}: tag {t.title} still set")
        have = node.get("properties") or {}
        for p, enc in props:
            actual = have.get(p.title)
            if enc is None:
                if actual not in (None, [], ""):
                    self.problems.append(f"{uuid}: {p.title} still set ({actual!r})")
            elif not _same(enc.expected, actual):
                self.problems.append(f"{uuid}: {p.title} is {actual!r}, expected {enc.expected!r}")
        return node

    def verify_tree(self, blocks: list[PreparedBlock]) -> list[dict]:
        out = []
        for b in blocks:
            node = self.verify_node(b.uuid, b.title, b.tags, (), b.props)
            if node:
                out.append({"uuid": b.uuid, "title": node.get("title")})
            out.extend(self.verify_tree(b.children))
        return out

    def result(self, **extra) -> dict:
        return {**extra, "verified": not self.problems, "problems": self.problems}


def _norm_title(t: str | None) -> str:
    return " ".join((t or "").split()).lower()


def _key(x: Any) -> Any:
    if isinstance(x, dict):
        return x.get("uuid")
    return x.lower() if isinstance(x, str) else x


def _same(expected: Any, actual: Any) -> bool:
    if isinstance(expected, list) or isinstance(actual, list):
        e = expected if isinstance(expected, list) else [expected]
        a = actual if isinstance(actual, list) else ([] if actual is None else [actual])
        return sorted(map(str, map(_key, e))) == sorted(map(str, map(_key, a)))
    return _key(expected) == _key(actual)


def outline_ancestors(graph, e: dict) -> set[int]:
    """Ids of the blocks above ``e`` (not the page)."""
    if "page" not in e:
        return set()
    blocks = {b["id"]: b for b in graph.page_blocks(e["page"]["id"])}
    out, cur = set(), e
    while cur and (cur.get("parent") or {}).get("id") in blocks:
        pid = cur["parent"]["id"]
        out.add(pid)
        cur = blocks[pid]
    return out

