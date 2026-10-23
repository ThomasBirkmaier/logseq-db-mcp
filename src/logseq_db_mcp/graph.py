"""Reading the graph: entities, outlines and their JSON form.

All reads go through Datascript ``pull [*]``, so every entity has one shape
regardless of which plugin-API call would have returned it.
"""

from __future__ import annotations

import logging
from typing import Any, Iterable

from . import refs, values
from .client import LogseqClient, edn_int, edn_ints, edn_uuids
from .schema import SchemaCache, SchemaError
from .settings import Settings

logger = logging.getLogger("logseq-db-mcp")

CLOSED_VALUE = "closed-value-property"


def is_value_block(e: dict) -> bool:
    return values.VALUE_BLOCK in e


def is_outline_block(e: dict) -> bool:
    """A block that belongs to the outline (not a property value or a choice)."""
    return "page" in e and not is_value_block(e) and CLOSED_VALUE not in e


class Graph:
    def __init__(self, client: LogseqClient, schema: SchemaCache, settings: Settings):
        self.client = client
        self.schema = schema
        self.settings = settings

    # --- entities -----------------------------------------------------------

    def pull_ids(self, ids: Iterable[int]) -> dict[int, dict]:
        ids = sorted({int(i) for i in ids})
        if not ids:
            return {}
        rows = self.client.q("[:find (pull ?e [*]) :in $ [?e ...]]", edn_ints(ids))
        return {e["id"]: e for (e,) in rows if isinstance(e, dict) and "id" in e}

    def pull_uuids(self, uuids: Iterable[str]) -> dict[str, dict]:
        uuids = sorted(set(uuids))
        if not uuids:
            return {}
        rows = self.client.q("[:find (pull ?e [*]) :in $ [?u ...] :where [?e :block/uuid ?u]]", edn_uuids(uuids))
        return {e["uuid"]: e for (e,) in rows if isinstance(e, dict) and "uuid" in e}

    def entity_by_uuid(self, uuid: str) -> dict | None:
        return self.pull_uuids([uuid]).get(uuid)

    def require(self, uuid: str) -> dict:
        e = self.entity_by_uuid(uuid)
        if e is None:
            raise LookupError(f"no node with UUID {uuid}")
        return e

    def titled_entities(self) -> list[tuple[int, str]]:
        """(id, title) of every page-like entity: pages, journals, tags, properties."""
        return [tuple(r) for r in self.client.q(
            "[:find ?e ?t :where [?e :block/title ?t] [(missing? $ ?e :block/page)]]")]

    def entities_by_title(self, title: str) -> list[dict]:
        """Page-like entities with exactly this title, ignoring case."""
        wanted = title.strip().lower()
        ids = [i for i, t in self.titled_entities() if isinstance(t, str) and t.lower() == wanted]
        return list(self.pull_ids(ids).values())

    def journal(self, iso_date: str, create: bool = False) -> dict | None:
        day = int(iso_date.replace("-", ""))
        query = "[:find ?e :in $ ?d :where [?e :block/journal-day ?d]]"
        rows = self.client.q(query, edn_int(day))
        if not rows and create:
            self.client.call("logseq.Editor.createJournalPage", iso_date)
            rows = self.client.q(query, edn_int(day))
        if not rows:
            return None
        return self.pull_ids([rows[0][0]]).get(rows[0][0])

    def tag_ids(self, e: dict) -> set[int]:
        return {t["id"] for t in e.get("tags") or [] if isinstance(t, dict)}

    def allowed_tag_ids(self, class_ids: list[int]) -> set[int]:
        schema = self.schema.get()
        allowed: set[int] = set()
        for c in class_ids:
            allowed |= schema.subtags(c)
        return allowed

    def kind(self, e: dict) -> str:
        if "journal-day" in e:
            return "journal"
        schema = self.schema.get()
        ids = self.tag_ids(e)
        if schema.kind_class_id(":logseq.class/Tag") in ids:
            return "tag"
        if schema.kind_class_id(":logseq.class/Property") in ids:
            return "property"
        return "block" if "page" in e else "page"

    # --- outline ------------------------------------------------------------

    def page_blocks(self, page_id: int) -> list[dict]:
        rows = self.client.q("[:find (pull ?b [*]) :in $ ?p :where [?b :block/page ?p]]", edn_int(page_id))
        return [b for (b,) in rows if isinstance(b, dict) and is_outline_block(b)]

    @staticmethod
    def children_map(blocks: list[dict]) -> dict[int, list[dict]]:
        by_parent: dict[int, list[dict]] = {}
        for b in blocks:
            parent = (b.get("parent") or {}).get("id")
            if parent is not None:
                by_parent.setdefault(parent, []).append(b)
        for kids in by_parent.values():
            kids.sort(key=lambda b: b.get("order") or "")
        return by_parent

    def children(self, e: dict) -> list[dict]:
        """Ordered outline children of a page or block."""
        page_id = e["id"] if "page" not in e else e["page"]["id"]
        return self.children_map(self.page_blocks(page_id)).get(e["id"], [])

    def page_of(self, e: dict) -> dict:
        if "page" not in e:
            return e
        return self.pull_ids([e["page"]["id"]]).get(e["page"]["id"], {})

    # --- access -------------------------------------------------------------

    def tag_ids_for_names(self, names: tuple[str, ...]) -> set[int]:
        ids: set[int] = set()
        for n in names:
            try:
                ids.add(self.schema.tag(n).id)
            except SchemaError:
                logger.warning(f"Access rule names unknown tag '{n}'; ignored")
        return ids

    def hidden(self, e: dict, page: dict | None = None) -> bool:
        if not self.settings.hide_tags:
            return False
        hide = self.tag_ids_for_names(self.settings.hide_tags)
        page = page if page is not None else self.page_of(e)
        return bool((self.tag_ids(e) | self.tag_ids(page)) & hide)

    # --- JSON form ----------------------------------------------------------

    def serialize(self, nodes: list[dict], depth: int = 0, include_idents: bool = False) -> list[dict]:
        """Nodes → compact JSON, with ``depth`` levels of outline children."""
        schema = self.schema.get()
        trees: dict[int, dict[int, list[dict]]] = {}
        if depth > 0:
            for n in nodes:
                pid = n["id"] if "page" not in n else n["page"]["id"]
                if pid not in trees:
                    trees[pid] = self.children_map(self.page_blocks(pid))

        # everything the output refers to, loaded in one go
        all_nodes = list(nodes)
        for n in nodes:
            all_nodes.extend(self._descendants(n, trees, depth))
        ref_ids: set[int] = set()
        uuids: set[str] = set()
        for n in all_nodes:
            ref_ids |= self.tag_ids(n)
            if "page" in n:
                ref_ids.add(n["page"]["id"])
            for key, raw in n.items():
                if key in schema.properties:
                    ref_ids.update(values.referenced_ids(raw))
            uuids |= refs.uuids_in(n.get("title"))
        known = {n["id"]: n for n in all_nodes}
        ents = {**known, **self.pull_ids(ref_ids - set(known))}
        # references stored as text value blocks holding a UUID (API-BEFUNDE I10)
        for n in all_nodes:
            for key, raw in n.items():
                p = schema.properties.get(key)
                if p is not None:
                    for i in values.referenced_ids(raw):
                        target = values.text_ref_uuid(p, ents.get(i, {}))
                        if target:
                            uuids.add(target)
        titles = {e["uuid"]: e.get("title") for e in ents.values() if "uuid" in e}
        missing = uuids - set(titles)
        by_uuid = {e["uuid"]: e for e in ents.values() if "uuid" in e}
        if missing:
            pulled = self.pull_uuids(missing)
            by_uuid.update(pulled)
            titles.update({u: e.get("title") for u, e in pulled.items()})

        hide = self.tag_ids_for_names(self.settings.hide_tags) if self.settings.hide_tags else set()

        def node_json(n: dict, level: int) -> dict | None:
            page_tags = self.tag_ids(ents.get(n["page"]["id"], {})) if "page" in n else set()
            if hide and (self.tag_ids(n) | page_tags) & hide:
                return None
            kind = self.kind(n)
            out: dict[str, Any] = {"uuid": n.get("uuid"), "kind": kind,
                                   "title": refs.render(n.get("title"), titles)}
            if kind == "journal":
                out["date"] = values.day_to_iso(n["journal-day"])
            if "page" in n:
                page = ents.get(n["page"]["id"], {})
                out["page"] = {"uuid": page.get("uuid"), "title": page.get("title")}
                parent = (n.get("parent") or {}).get("id")
                if parent is not None and parent != n["page"]["id"]:
                    out["parent"] = ents.get(parent, {}).get("uuid") or known.get(parent, {}).get("uuid")
            tags = [ents[t] for t in self.tag_ids(n) if t in ents and schema.is_shown_tag(t)]
            if tags:
                out["tags"] = [{"uuid": t["uuid"], "title": t.get("title"),
                                **({"ident": t.get("ident")} if include_idents else {})} for t in tags]
            props = {}
            for key, raw in n.items():
                p = schema.properties.get(key)
                if p is None or not p.shown:
                    continue
                value = values.decode(p, raw, ents, by_uuid)
                props[p.title] = {"ident": p.ident, "value": value} if include_idents else value
            if props:
                out["properties"] = props
            if "updated-at" in n:
                out["updated"] = values.ms_to_iso(n["updated-at"])
            if level < depth:
                kids = self._kids(n, trees)
                rendered = [k for k in (node_json(c, level + 1) for c in kids) if k is not None]
                if rendered:
                    out["children"] = rendered
            elif depth and self._kids(n, trees):
                out["has_children"] = True
            return out

        return [j for j in (node_json(n, 0) for n in nodes) if j is not None]

    @staticmethod
    def _kids(n: dict, trees: dict[int, dict[int, list[dict]]]) -> list[dict]:
        pid = n["id"] if "page" not in n else n["page"]["id"]
        return trees.get(pid, {}).get(n["id"], [])

    def _descendants(self, n: dict, trees, depth: int) -> list[dict]:
        if depth <= 0:
            return []
        out = []
        for c in self._kids(n, trees):
            out.append(c)
            out.extend(self._descendants(c, trees, depth - 1))
        return out
