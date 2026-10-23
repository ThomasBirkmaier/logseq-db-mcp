"""Schema tools: create and change properties, change tags (docs/API-BEFUNDE.md F2, F11, I16 to I20).

Each goes the way that was measured to work, not the one the plugin API
names: properties are created through Logseq's own MCP (``upsertNodes``,
gives ``user.property``), attached to tags by writing the tag's
``:logseq.property.class/properties`` directly, renamed with ``renamePage``.
Tags created here land in the plugin namespace (``:plugin.class._test_plugin/…``,
F5); in the UI they behave like tags made there (checked by Thomas in SpikeTest).
"""

from __future__ import annotations

from typing import Any

from ..access import AccessDenied
from ..graph import DELETED_AT
from ..schema import Property, Tag
from ..values import InvalidValue
from ..writer import Writer
from .base import Context, Tool

CLASS_PROPERTIES = ":logseq.property.class/properties"
CLASSES = ":logseq.property/classes"
TYPES = {"text": "default", "default": "default", "number": "number", "checkbox": "checkbox", "url": "url",
         "date": "date", "datetime": "datetime", "node": "node"}

DRY_RUN = {"type": "boolean", "default": False, "description": "Validate and show the plan without writing"}


def _guard(ctx: Context) -> None:
    # A schema change affects every page; page- or tag-based write rules cannot limit it.
    if ctx.settings.has_access_rules:
        raise AccessDenied("Access denied: schema changes are disabled while access rules are set.")


def _user_tag(ctx: Context, name: str) -> Tag:
    t = ctx.schema.tag(name)
    if t.builtin:
        raise InvalidValue(f"'{t.title}' is a built-in tag of Logseq and cannot be changed")
    return t


def _user_property(ctx: Context, name: str) -> Property:
    p = ctx.schema.property(name)
    if p.builtin:
        raise InvalidValue(f"'{p.title}' is a built-in property of Logseq and cannot be changed")
    return p


def _free_title(ctx: Context, title: str, what: str) -> None:
    for e in ctx.graph.entities_by_title(title):
        where = " (in Logseq's recycle bin)" if DELETED_AT in e else ""
        raise InvalidValue(f"'{title}' already exists{where}; {what} needs a title of its own")


class _SchemaWriter(Writer):
    """Writer with the schema steps; every step is recorded for PartialWrite."""

    def set_class_properties(self, tag: Tag, prop_ids: list[int]) -> None:
        # The list can only be extended, so: clear it, then write the wanted set (I16).
        old = ", ".join(self.ctx.schema.get().properties_by_id[i].title
                        for i in tag.properties if i in self.ctx.schema.get().properties_by_id)
        self._call(f"clear class properties of {tag.title} (were: {old or 'none'})",
                   "logseq.Editor.removeBlockProperty", tag.uuid, CLASS_PROPERTIES)
        if prop_ids:
            self._call(f"set class properties of {tag.title}", "logseq.Editor.upsertBlockProperty",
                       tag.uuid, CLASS_PROPERTIES, prop_ids, {"entityId": True})

    def set_allowed_tags(self, prop: Property, tag_ids: list[int]) -> None:
        if tag_ids:
            self._call(f"allowed tags of {prop.title}", "logseq.Editor.setPropertyNodeTags", prop.uuid, tag_ids)
        else:
            self._call(f"clear allowed tags of {prop.title}", "logseq.Editor.removeBlockProperty", prop.uuid, CLASSES)

    def add_choices(self, prop: Property, titles: list[str]) -> None:
        uuids = []
        for t in titles:
            page = self._call(f"create choice '{t}'", "logseq.Editor.createPage", t, {}, {"createFirstBlock": False})
            uuids.append(page["uuid"])
        self._call(f"choices of {prop.title}", "logseq.Editor.addPropertyValueChoices", prop.uuid, uuids)
        for u in uuids:  # createPage leaves an empty block behind (I4)
            e = self.graph.require(u)
            for (b,) in self.client.q("[:find (pull ?b [*]) :in $ ?p :where [?b :block/parent ?p]]", str(e["id"])):
                if (b.get("title") or "") == "":
                    self.client.call("logseq.Editor.removeBlock", b["uuid"])

    def check_choices(self, titles: list[str]) -> list[str]:
        clean = [t.strip() for t in titles if t and t.strip()]
        if len({t.lower() for t in clean}) != len(clean):
            raise InvalidValue("choices must be distinct")
        for t in clean:
            # an existing page would itself become the choice and leave its place (I1)
            _free_title(self.ctx, t, "a choice")
        return clean

    def reload(self):
        self.ctx.schema.invalidate()
        return self.ctx.schema.get()


def _tag_ids(ctx: Context, names: list[str]) -> list[int]:
    return [ctx.schema.tag(n).id for n in names]


class CreateProperty(Tool):
    name = "create_property"
    writes = True
    description = (
        "Create a property like one made in the Logseq UI (user.property): type, cardinality, for node "
        "properties the allowed tags, for text properties a fixed list of choices, and optionally attach "
        "it to tags as class property. Tags themselves can only be created in the UI.")
    input_schema = {"type": "object", "properties": {
        "title": {"type": "string", "minLength": 1},
        "type": {"enum": sorted(TYPES), "default": "text"},
        "cardinality": {"enum": ["one", "many"], "default": "one"},
        "classes": {"type": "array", "items": {"type": "string"},
                    "description": "node only: tags a value must carry"},
        "choices": {"type": "array", "items": {"type": "string"},
                    "description": "text only: the allowed values; each becomes a new node"},
        "tags": {"type": "array", "items": {"type": "string"}, "description": "attach as class property"},
        "dry_run": DRY_RUN},
        "required": ["title"]}

    def run(self, ctx: Context, args: dict) -> Any:
        _guard(ctx)
        w = _SchemaWriter(ctx, args.get("dry_run", False))
        title = args["title"].strip()
        ptype = TYPES[args.get("type", "text")]
        many = args.get("cardinality", "one") == "many"
        _free_title(ctx, title, "a property")
        classes = _tag_ids(ctx, args.get("classes") or [])
        if classes and ptype != "node":
            raise InvalidValue("classes only apply to node properties")
        choices = w.check_choices(args.get("choices") or [])
        if choices and ptype != "default":
            raise InvalidValue("choices are supported for text properties")
        tags = [_user_tag(ctx, n) for n in args.get("tags") or []]
        plan = {"title": title, "type": ptype, "cardinality": "many" if many else "one",
                "classes": [ctx.schema.get().tags[i].title for i in classes], "choices": choices,
                "attach_to": [t.title for t in tags]}
        if w.dry_run:
            return {"dry_run": True, "property": plan}

        try:
            ctx.client.mcp_tool("upsertNodes", {"operations": [{"operation": "add", "entityType": "property", "id": None,
                                                              "data": {"title": title, "property-type": ptype,
                                                                       "property-cardinality": "many" if many else "one"}}]})
        except Exception as e:
            raise InvalidValue(f"Logseq refused to create '{title}': {e}") from e
        w.done.append(f"create property '{title}'")
        schema = w.reload()
        hits = [p for p in schema.properties.values() if p.title == title and not p.builtin]
        if len(hits) != 1:
            raise InvalidValue(f"created '{title}', but found {len(hits)} properties with that title afterwards")
        prop = hits[0]
        if classes:
            w.set_allowed_tags(prop, classes)
        if choices:
            w.add_choices(prop, choices)
        for t in tags:
            t = w.reload().tags[t.id]
            w.set_class_properties(t, [*t.properties, prop.id])
        return self._verify(w, prop, plan)

    @staticmethod
    def _verify(w: _SchemaWriter, prop: Property, plan: dict) -> dict:
        schema = w.reload()
        p = schema.properties.get(prop.ident)
        if p is None:
            w.problems.append("property not found after writing")
        else:
            got = {"type": p.type, "cardinality": "many" if p.many else "one",
                   "classes": sorted(schema.tags[i].title for i in p.classes if i in schema.tags),
                   "choices": sorted(p.choices.values()),
                   "attach_to": sorted(t.title for t in schema.tags.values() if p.id in t.properties)}
            for key, want in [("type", plan["type"]), ("cardinality", plan["cardinality"]),
                              ("classes", sorted(plan["classes"])), ("choices", sorted(plan["choices"])),
                              ("attach_to", sorted(plan["attach_to"]))]:
                if got[key] != want and not (key == "attach_to" and set(want) <= set(got[key])):
                    w.problems.append(f"{key} is {got[key]!r}, expected {want!r}")
        return w.result(property={"title": prop.title, "uuid": prop.uuid, "ident": prop.ident},
                        changed=[prop.uuid])


class UpdateProperty(Tool):
    name = "update_property"
    writes = True
    description = (
        "Change a property: rename it (values stay), replace its allowed tags (node properties; [] removes "
        "the restriction), or add choices (text properties). Type and cardinality cannot be changed.")
    input_schema = {"type": "object", "properties": {
        "property": {"type": "string", "minLength": 1, "description": "title, ident or UUID"},
        "title": {"type": "string", "minLength": 1},
        "classes": {"type": "array", "items": {"type": "string"}},
        "add_choices": {"type": "array", "items": {"type": "string"}},
        "dry_run": DRY_RUN},
        "required": ["property"]}

    def run(self, ctx: Context, args: dict) -> Any:
        _guard(ctx)
        w = _SchemaWriter(ctx, args.get("dry_run", False))
        prop = _user_property(ctx, args["property"])
        schema = ctx.schema.get()
        plan: dict = {}
        new_title = (args.get("title") or "").strip()
        if new_title and new_title != prop.title:
            _free_title(ctx, new_title, "a property")
            plan["title"] = {"old": prop.title, "new": new_title}
        if "classes" in args:
            if prop.type not in ("node",):
                raise InvalidValue("classes only apply to node properties")
            classes = _tag_ids(ctx, args["classes"])
            plan["classes"] = {"old": sorted(schema.tags[i].title for i in prop.classes if i in schema.tags),
                               "new": sorted(schema.tags[i].title for i in classes)}
        choices = w.check_choices(args.get("add_choices") or [])
        if choices:
            if prop.type != "default":
                raise InvalidValue("choices are supported for text properties")
            plan["add_choices"] = choices
        if not plan:
            raise InvalidValue("nothing to change: give title, classes or add_choices")
        if w.dry_run:
            return {"dry_run": True, "property": prop.title, "changes": plan}

        if "title" in plan:
            w._call(f"rename property {prop.title}", "logseq.Editor.renamePage", prop.uuid, new_title)
        if "classes" in plan:
            w.set_allowed_tags(prop, classes)
        if choices:
            w.add_choices(prop, choices)
        p = w.reload().properties.get(prop.ident)
        if p is None:
            w.problems.append("property not found after writing")
        else:
            s = ctx.schema.get()
            if "title" in plan and p.title != new_title:
                w.problems.append(f"title is {p.title!r}")
            if "classes" in plan and sorted(s.tags[i].title for i in p.classes if i in s.tags) != plan["classes"]["new"]:
                w.problems.append(f"allowed tags are {[s.tags[i].title for i in p.classes if i in s.tags]}")
            if choices and not set(c.lower() for c in choices) <= {c.lower() for c in p.choices.values()}:
                w.problems.append(f"choices are {list(p.choices.values())}")
        return w.result(property={"title": (p or prop).title, "uuid": prop.uuid}, changes=plan, changed=[prop.uuid])


class UpdateTag(Tool):
    name = "update_tag"
    writes = True
    description = (
        "Change a tag (class): rename it, its class properties (add, remove, or set the exact list) and "
        "what it extends. Works for tags created in the UI as well.")
    input_schema = {"type": "object", "properties": {
        "tag": {"type": "string", "minLength": 1},
        "title": {"type": "string", "minLength": 1, "description": "new name; objects keep the tag"},
        "add_properties": {"type": "array", "items": {"type": "string"}},
        "remove_properties": {"type": "array", "items": {"type": "string"}},
        "properties": {"type": "array", "items": {"type": "string"},
                       "description": "the exact list of own class properties (instead of add/remove)"},
        "add_extends": {"type": "array", "items": {"type": "string"}},
        "remove_extends": {"type": "array", "items": {"type": "string"}},
        "dry_run": DRY_RUN},
        "required": ["tag"]}

    def run(self, ctx: Context, args: dict) -> Any:
        _guard(ctx)
        w = _SchemaWriter(ctx, args.get("dry_run", False))
        tag = _user_tag(ctx, args["tag"])
        schema = ctx.schema.get()

        def title(pid: int) -> str:
            return schema.properties_by_id[pid].title if pid in schema.properties_by_id else str(pid)

        plan: dict = {}
        new_title = (args.get("title") or "").strip()
        if new_title and new_title != tag.title:
            _free_title(ctx, new_title, "a tag")
            plan["title"] = {"old": tag.title, "new": new_title}

        if "properties" in args and (args.get("add_properties") or args.get("remove_properties")):
            raise InvalidValue("give either properties or add_properties/remove_properties")
        current = list(tag.properties)
        if "properties" in args:
            wanted = [ctx.schema.property(n).id for n in args["properties"]]
        else:
            wanted = list(current)
            for n in args.get("add_properties") or []:
                pid = ctx.schema.property(n).id
                if pid not in wanted:
                    wanted.append(pid)
            for n in args.get("remove_properties") or []:
                pid = ctx.schema.property(n).id
                if pid not in current:
                    raise InvalidValue(f"'{title(pid)}' is not a class property of {tag.title}")
                wanted.remove(pid)
        if len(set(wanted)) != len(wanted):
            raise InvalidValue("a property is listed twice")
        for pid in wanted:
            if schema.properties_by_id[pid].builtin:
                raise InvalidValue(f"'{title(pid)}' is built in and cannot be a class property here")
        if wanted != current:
            plan["properties"] = {"old": [title(i) for i in current], "new": [title(i) for i in wanted]}

        add_ext = [_user_or_any_tag(ctx, n) for n in args.get("add_extends") or []]
        rem_ext = [ctx.schema.tag(n) for n in args.get("remove_extends") or []]
        below = schema.subtags(tag.id)
        for t in add_ext:
            if t.id in below:
                raise InvalidValue(f"{tag.title} cannot extend {t.title}: that would make a cycle")
        for t in rem_ext:
            if t.id not in tag.extends:
                raise InvalidValue(f"{tag.title} does not extend {t.title}")
        add_ext = [t for t in add_ext if t.id not in tag.extends]
        if add_ext or rem_ext:
            plan["extends"] = {"add": [t.title for t in add_ext], "remove": [t.title for t in rem_ext]}
        if not plan:
            raise InvalidValue("nothing to change")
        if w.dry_run:
            return {"dry_run": True, "tag": tag.title, "changes": plan}

        if "title" in plan:
            w._call(f"rename tag {tag.title}", "logseq.Editor.renamePage", tag.uuid, new_title)
        if "properties" in plan:
            w.set_class_properties(tag, wanted)
        for t in add_ext:
            w._call(f"{tag.title} extends {t.title}", "logseq.Editor.addTagExtends", tag.uuid, t.uuid)
        for t in rem_ext:
            w._call(f"{tag.title} no longer extends {t.title}", "logseq.Editor.removeTagExtends", tag.uuid, t.uuid)

        after = w.reload().tags.get(tag.id)
        if after is None:
            w.problems.append("tag not found after writing")
        else:
            if "title" in plan and after.title != new_title:
                w.problems.append(f"title is {after.title!r}")
            if "properties" in plan and sorted(after.properties) != sorted(wanted):
                w.problems.append(f"class properties are {[title(i) for i in after.properties]}")
            for t in add_ext:
                if t.id not in after.extends:
                    w.problems.append(f"does not extend {t.title}")
            for t in rem_ext:
                if t.id in after.extends:
                    w.problems.append(f"still extends {t.title}")
        return w.result(tag={"title": (after or tag).title, "uuid": tag.uuid}, changes=plan, changed=[tag.uuid])


class CreateTag(Tool):
    name = "create_tag"
    writes = True
    description = (
        "Create a tag (class), optionally extending other tags and with class properties. The tag gets an "
        "ident in the plugin namespace (:plugin.class._test_plugin/…), not :user.class/… as in the UI; it "
        "works the same in Logseq, but ask Thomas before creating tags in ReqSeq.")
    input_schema = {"type": "object", "properties": {
        "title": {"type": "string", "minLength": 1},
        "extends": {"type": "array", "items": {"type": "string"}},
        "properties": {"type": "array", "items": {"type": "string"}, "description": "class properties"},
        "dry_run": DRY_RUN},
        "required": ["title"]}

    def run(self, ctx: Context, args: dict) -> Any:
        _guard(ctx)
        title = args["title"].strip()
        _free_title(ctx, title, "a tag")
        extends = [_user_or_any_tag(ctx, n) for n in args.get("extends") or []]
        props = [_user_property(ctx, n) for n in args.get("properties") or []]
        if len({p.id for p in props}) != len(props):
            raise InvalidValue("a property is listed twice")
        plan = {"title": title, "extends": [t.title for t in extends], "properties": [p.title for p in props]}
        if args.get("dry_run"):
            return {"dry_run": True, "tag": plan}

        w = _SchemaWriter(ctx)
        created = w._call(f"create tag '{title}'", "logseq.Editor.createTag", title)
        if not isinstance(created, dict) or not created.get("uuid"):
            raise InvalidValue(f"Logseq did not create the tag '{title}' (answer: {created!r})")
        w.reload()
        result = {"tag": {"title": title, "uuid": created["uuid"], "ident": created.get("ident")},
                  "verified": True, "problems": []}
        if extends or props:
            changes = {"tag": created["uuid"]}
            if extends:
                changes["add_extends"] = [t.uuid for t in extends]
            if props:
                changes["properties"] = [p.ident for p in props]
            more = UpdateTag().run(ctx, changes)
            result["verified"], result["problems"] = more["verified"], more["problems"]
        t = ctx.schema.get().tags.get(ctx.schema.tag(created["uuid"]).id)
        if t is None or t.title != title:
            result["verified"] = False
            result["problems"].append(f"tag is {getattr(t, 'title', None)!r} after writing")
        return {**result, "changed": [created["uuid"]]}


def _user_or_any_tag(ctx: Context, name: str) -> Tag:
    # extending a built-in class (e.g. Task) is legitimate; only Logseq's kind classes are not
    t = ctx.schema.tag(name)
    if t.ident in (":logseq.class/Tag", ":logseq.class/Property", ":logseq.class/Page", ":logseq.class/Journal",
                   ":logseq.class/Root"):
        raise InvalidValue(f"'{t.title}' cannot be extended")
    return t


SCHEMA_TOOLS = [CreateTag(), UpdateTag(), CreateProperty(), UpdateProperty()]
