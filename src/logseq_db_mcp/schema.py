"""Tags (classes) and properties of the graph, read once and kept in memory.

Everything is read via Datascript ``pull [*]``: core attributes come back
without namespace (``uuid``, ``title``, ``ident``, ``cardinality``), all
others with it (``:logseq.property/type``, ``:logseq.property.class/extends``).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from .client import LogseqClient

logger = logging.getLogger("logseq-db-mcp")

PROPERTIES_Q = "[:find (pull ?p [*]) :where [?c :db/ident :logseq.class/Property] [?p :block/tags ?c]]"
TAGS_Q = "[:find (pull ?t [*]) :where [?c :db/ident :logseq.class/Tag] [?t :block/tags ?c]]"
CHOICES_Q = "[:find (pull ?v [*]) :where [?v :block/closed-value-property _]]"

# Built-in properties worth showing on nodes; all others stay hidden.
SHOWN_BUILTINS = frozenset({
    ":logseq.property/status",
    ":logseq.property/priority",
    ":logseq.property/deadline",
    ":logseq.property/scheduled",
})

# Classes that only say what kind a node is; shown as ``kind``, not as tags.
KIND_CLASSES = frozenset({
    ":logseq.class/Page", ":logseq.class/Journal", ":logseq.class/Tag", ":logseq.class/Property",
})

REF_TYPES = frozenset({"node", "page", "class", "entity", "date", "property"})


class SchemaError(ValueError):
    """Unknown or ambiguous tag or property."""


def _ids(refs) -> list[int]:
    if isinstance(refs, dict):
        refs = [refs]
    return [r["id"] for r in refs or [] if isinstance(r, dict) and "id" in r]


@dataclass
class Property:
    id: int
    uuid: str
    ident: str
    title: str
    type: str
    many: bool
    builtin: bool
    hidden: bool
    classes: list[int] = field(default_factory=list)
    # closed values: entity id → title
    choices: dict[int, str] = field(default_factory=dict)

    @property
    def shown(self) -> bool:
        if self.hidden:
            return False
        return not self.builtin or self.ident in SHOWN_BUILTINS


@dataclass
class Tag:
    id: int
    uuid: str
    ident: str
    title: str
    builtin: bool
    extends: list[int] = field(default_factory=list)
    properties: list[int] = field(default_factory=list)


class Schema:
    def __init__(self, properties: list[Property], tags: list[Tag]):
        self.properties = {p.ident: p for p in properties}
        self.properties_by_id = {p.id: p for p in properties}
        self.tags = {t.id: t for t in tags}
        self.tag_ids_by_ident = {t.ident: t.id for t in tags}

    # --- loading ------------------------------------------------------------

    @classmethod
    def load(cls, client: LogseqClient) -> "Schema":
        props = []
        for (e,) in client.q(PROPERTIES_Q):
            ident = e.get("ident")
            if not isinstance(ident, str) or not ident.startswith(":"):
                # Core attributes such as "tags" are properties too, but not
                # values a tool may set directly.
                continue
            props.append(Property(
                id=e["id"], uuid=e["uuid"], ident=ident, title=e.get("title") or ident,
                type=e.get(":logseq.property/type", "default"),
                many=e.get("cardinality") == ":db.cardinality/many",
                builtin=bool(e.get(":logseq.property/built-in?")),
                hidden=bool(e.get(":logseq.property/hide?")),
                classes=_ids(e.get(":logseq.property/classes")),
            ))
        tags = [
            Tag(id=e["id"], uuid=e["uuid"], ident=e.get("ident", ""), title=e.get("title", ""),
                builtin=bool(e.get(":logseq.property/built-in?")),
                extends=_ids(e.get(":logseq.property.class/extends")),
                properties=_ids(e.get(":logseq.property.class/properties")))
            for (e,) in client.q(TAGS_Q)
        ]
        schema = cls(props, tags)
        for (v,) in client.q(CHOICES_Q):
            for pid in _ids(v.get("closed-value-property")):
                p = schema.properties_by_id.get(pid)
                if p:
                    p.choices[v["id"]] = v.get("title", str(v.get(":logseq.property/value", "")))
        logger.info(f"Schema loaded: {len(schema.properties)} properties, {len(schema.tags)} tags")
        return schema

    # --- lookup -------------------------------------------------------------

    def property(self, key: str) -> Property:
        """By ident, UUID or title (case-insensitive). Ambiguous titles are an error."""
        if key in self.properties:
            return self.properties[key]
        hits = [p for p in self.properties.values()
                if key == p.uuid or key.lower() == p.title.lower() or (":" + key) == p.ident]
        if len(hits) == 1:
            return hits[0]
        if not hits:
            raise SchemaError(f"Unknown property '{key}'. get_schema lists the properties.")
        raise SchemaError(f"Property title '{key}' is ambiguous: {', '.join(p.ident for p in hits)}; use the ident.")

    def tag(self, key: str) -> Tag:
        """By ident, UUID or title (case-insensitive). Ambiguous titles are an error."""
        if key in self.tag_ids_by_ident:
            return self.tags[self.tag_ids_by_ident[key]]
        hits = [t for t in self.tags.values()
                if key == t.uuid or key.lower() == t.title.lower() or (":" + key) == t.ident]
        if len(hits) == 1:
            return hits[0]
        if not hits:
            raise SchemaError(f"Unknown tag '{key}'. get_schema lists the tags.")
        raise SchemaError(f"Tag title '{key}' is ambiguous: {', '.join(t.ident for t in hits)}; use the ident.")

    def kind_class_id(self, ident: str) -> int | None:
        return self.tag_ids_by_ident.get(ident)

    def subtags(self, tag_id: int) -> set[int]:
        """The tag and every tag that extends it, over any number of levels."""
        result, frontier = {tag_id}, [tag_id]
        while frontier:
            current = frontier.pop()
            for t in self.tags.values():
                if current in t.extends and t.id not in result:
                    result.add(t.id)
                    frontier.append(t.id)
        return result

    def supertags(self, tag_id: int) -> list[int]:
        """Ancestors, nearest first, without the tag itself and without Root."""
        result, frontier = [], list(self.tags[tag_id].extends) if tag_id in self.tags else []
        while frontier:
            current = frontier.pop(0)
            t = self.tags.get(current)
            if not t or current in result or t.ident == ":logseq.class/Root":
                continue
            result.append(current)
            frontier.extend(t.extends)
        return result

    def class_properties(self, tag_id: int) -> list[Property]:
        """Own and inherited class properties of a tag."""
        ids: list[int] = []
        for tid in [tag_id, *self.supertags(tag_id)]:
            for pid in self.tags[tid].properties:
                if pid not in ids:
                    ids.append(pid)
        return [self.properties_by_id[i] for i in ids if i in self.properties_by_id]

    def is_shown_tag(self, tag_id: int) -> bool:
        t = self.tags.get(tag_id)
        return t is not None and t.ident not in KIND_CLASSES


class SchemaCache:
    """Holds the schema; reloads after own schema writes or an unknown name."""

    def __init__(self, client: LogseqClient):
        self.client = client
        self._schema: Schema | None = None

    def get(self) -> Schema:
        if self._schema is None:
            self._schema = Schema.load(self.client)
        return self._schema

    def invalidate(self) -> None:
        self._schema = None

    def covering(self, entities) -> Schema:
        """The schema, reloaded once if an entity carries a property it does not know yet
        (created in the UI or by another client since the last load)."""
        schema = self.get()
        for e in entities:
            for key in e:
                if key.startswith((":user.property/", ":plugin.property.")) and key not in schema.properties:
                    self.invalidate()
                    return self.get()
        return schema

    def property(self, key: str) -> Property:
        try:
            return self.get().property(key)
        except SchemaError:
            self.invalidate()  # created in the UI since the last load?
            return self.get().property(key)

    def tag(self, key: str) -> Tag:
        try:
            return self.get().tag(key)
        except SchemaError:
            self.invalidate()
            return self.get().tag(key)
