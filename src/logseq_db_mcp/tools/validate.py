"""validate_objects (phase 3): check objects against the schema, read-only."""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

from .. import values
from ..client import edn_ints
from ..graph import DELETED_AT, is_value_block
from ..schema import REF_TYPES, Property
from .base import Context, Tool

ISSUES = {
    "missing": "a class property of one of the object's tags is empty",
    "wrong_type": "the stored value does not fit the property type",
    "disallowed_tag": "a reference points to a node without any of the allowed tags",
    "dangling": "a reference points to a node that no longer exists",
    "recycled_target": "a reference points to a page in the recycle bin",
    "text_reference": "a reference is stored as text holding a UUID; update_objects with the same value repairs it",
    "duplicate": "several objects share a value of a property that should be unique",
}


class ValidateObjects(Tool):
    name = "validate_objects"
    description = (
        "Check the objects of a tag (default: including subtags) against the schema and list every "
        "problem: empty class properties, values of the wrong type, references to nodes without an "
        "allowed tag, to deleted or recycled nodes, references stored as text, and duplicate values of "
        "`unique` properties (default: the ID property). Read-only.")
    input_schema = {"type": "object", "properties": {
        "tag": {"type": "string", "minLength": 1},
        "include_subtags": {"type": "boolean", "default": True},
        "unique": {"type": "array", "items": {"type": "string"},
                   "description": "Properties whose values must be unique; default: the ID property"},
        "checks": {"type": "array", "items": {"enum": sorted(ISSUES)},
                   "description": "Only these checks; default all"},
        "limit": {"type": "integer", "minimum": 1, "maximum": 2000, "default": 500}},
        "required": ["tag"]}

    def run(self, ctx: Context, args: dict) -> Any:
        g, schema = ctx.graph, ctx.schema.get()
        tag = ctx.schema.tag(args["tag"])
        tag_ids = schema.subtags(tag.id) if args.get("include_subtags", True) else {tag.id}
        checks = set(args.get("checks") or ISSUES)
        rows = ctx.client.q("[:find ?b :in $ [?t ...] :where [?b :block/tags ?t]]", edn_ints(tag_ids))
        objects = [o for o in g.pull_ids(r[0] for r in rows).values()
                   if DELETED_AT not in o and not g.hidden(o)]

        # everything the objects' property values point to, in one go
        ref_ids = {i for o in objects for k, raw in o.items() if k in schema.properties
                   for i in values.referenced_ids(raw)}
        ents = g.pull_ids(ref_ids)
        text_targets = {t for o in objects for k, raw in o.items() if k in schema.properties
                        for i in values.referenced_ids(raw)
                        if (t := values.text_ref_uuid(schema.properties[k], ents.get(i, {})))}
        by_uuid = g.pull_uuids(text_targets)

        issues: list[dict] = []

        def report(o: dict, kind: str, p: Property | None, detail: str) -> None:
            issues.append({"uuid": o["uuid"], "title": o.get("title"), "issue": kind,
                           **({"property": p.title} if p else {}), "detail": detail})

        for o in objects:
            obj_tags = g.tag_ids(o) & set(schema.tags)
            required = {p.ident: p for t in obj_tags for p in schema.class_properties(t)}
            if "missing" in checks:
                for ident, p in required.items():
                    if not values.referenced_ids(o.get(ident)) and o.get(ident) in (None, [], ""):
                        owners = [schema.tags[t].title for t in obj_tags
                                  if any(q.ident == ident for q in schema.class_properties(t))]
                        report(o, "missing", p, f"required by {', '.join(sorted(owners))}")
            for key, raw in o.items():
                p = schema.properties.get(key)
                if p is None or not p.shown:
                    continue
                self._check_value(o, p, raw, ents, by_uuid, g, checks, report)

        if "duplicate" in checks:
            names = args.get("unique")
            if names is None:
                idp = g.id_property()
                names = [idp.title] if idp else []
            for name in names:
                p = ctx.schema.property(name)
                seen: dict[str, list[dict]] = defaultdict(list)
                blocks = g.pull_ids(i for o in objects for i in values.referenced_ids(o.get(p.ident)))
                for o in objects:
                    v = values.decode(p, o.get(p.ident), {**ents, **blocks})
                    for item in (v if isinstance(v, list) else [v]):
                        if item not in (None, ""):
                            key = item["uuid"] if isinstance(item, dict) else str(item)
                            seen[key].append(o)
                for value, holders in seen.items():
                    if len(holders) > 1:
                        for o in holders:
                            others = [h.get("title") for h in holders if h is not o]
                            report(o, "duplicate", p, f"'{value}' also on: {'; '.join(others)}")

        limit = args.get("limit", 500)
        issues.sort(key=lambda i: (i["issue"], (i.get("title") or "").lower(), i.get("property", "")))
        return {
            "tag": tag.title, "tags_checked": sorted(schema.tags[t].title for t in tag_ids),
            "objects": len(objects),
            "objects_with_issues": len({i["uuid"] for i in issues}),
            "summary": dict(Counter(i["issue"] for i in issues)),
            "issues": issues[:limit], "truncated": len(issues) > limit,
            "legend": {k: ISSUES[k] for k in sorted({i["issue"] for i in issues})},
        }

    @staticmethod
    def _check_value(o, p: Property, raw, ents, by_uuid, g, checks, report) -> None:
        refs = values.referenced_ids(raw)
        literals = [x for x in (raw if isinstance(raw, list) else [raw]) if not isinstance(x, dict)]
        if p.type in REF_TYPES and not p.choices:
            allowed = g.allowed_tag_ids(p.classes) if p.classes else set()
            for i in refs:
                e = ents.get(i)
                if e is None:
                    if "dangling" in checks:
                        report(o, "dangling", p, f"entity {i} does not exist")
                    continue
                target = e
                if is_value_block(e):
                    uuid = values.text_ref_uuid(p, e)
                    if uuid is None:
                        if "wrong_type" in checks:
                            report(o, "wrong_type", p, f"text {e.get('title')!r} instead of a reference")
                        continue
                    target = by_uuid.get(uuid)
                    if "text_reference" in checks:
                        report(o, "text_reference", p,
                               f"text '{uuid}' → {(target or {}).get('title', 'missing node')}")
                    if target is None:
                        if "dangling" in checks:
                            report(o, "dangling", p, f"no node with UUID {uuid}")
                        continue
                if DELETED_AT in target and "recycled_target" in checks:
                    report(o, "recycled_target", p, f"'{target.get('title')}' is in the recycle bin")
                if allowed and not (g.tag_ids(target) & allowed) and "disallowed_tag" in checks:
                    report(o, "disallowed_tag", p, f"'{target.get('title')}' has none of the allowed tags")
            if literals and "wrong_type" in checks:
                report(o, "wrong_type", p, f"literal {literals[0]!r} instead of a reference")
            return
        if "wrong_type" not in checks:
            return
        if p.type == "checkbox" and any(not isinstance(x, bool) for x in literals):
            report(o, "wrong_type", p, f"{literals!r} is not true/false")
        elif p.type == "datetime" and any(isinstance(x, bool) or not isinstance(x, (int, float)) for x in literals):
            report(o, "wrong_type", p, f"{literals!r} is not a time stamp")
        elif p.type == "number":
            for i in refs:
                v = (ents.get(i) or {}).get(":logseq.property/value")
                if ents.get(i) is not None and (isinstance(v, bool) or not isinstance(v, (int, float))):
                    report(o, "wrong_type", p, f"{(ents.get(i) or {}).get('title')!r} is not a number")
        elif p.choices:
            for i in refs:
                if i not in p.choices:
                    report(o, "wrong_type", p, f"value {(ents.get(i) or {}).get('title')!r} is not a choice")
