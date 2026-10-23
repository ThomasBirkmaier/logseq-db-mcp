"""create_property, update_property, update_tag in SpikeTest (API-BEFUNDE I16 to I20)."""

import pytest

from .conftest import Runner, make_settings

pytestmark = pytest.mark.live


def schema(tool):
    s = tool("get_schema")
    return {t["title"]: t for t in s["tags"]}, {p["title"]: p for p in s["properties"]}


def test_create_property_full(tool, model):
    p = model["p"]
    plan = tool("create_property", title=f"{p} Prio", type="text", choices=[f"{p} Hoch", f"{p} Niedrig"],
                tags=[f"{p} Andere"], dry_run=True)
    assert plan["dry_run"] and f"{p} Prio" not in schema(tool)[1]

    r = tool("create_property", title=f"{p} Prio", type="text", choices=[f"{p} Hoch", f"{p} Niedrig"],
             tags=[f"{p} Andere"])
    assert r["verified"], r["problems"]
    assert r["property"]["ident"].startswith(":user.property/")
    tags, props = schema(tool)
    assert sorted(props[f"{p} Prio"]["choices"]) == [f"{p} Hoch", f"{p} Niedrig"]
    assert f"{p} Prio" in tags[f"{p} Andere"]["properties"]

    r = tool("create_property", title=f"{p} Bezug", type="node", cardinality="many", classes=[f"{p} Andere"])
    assert r["verified"], r["problems"]
    assert schema(tool)[1][f"{p} Bezug"]["classes"] == [f"{p} Andere"]

    # the new properties work right away, with their rules
    page = tool("create_page", title=f"{p} Schema-Objekte")["page"]["uuid"]
    other = tool("create_objects", objects=[{"tags": [f"{p} Andere"], "title": "darf", "target": page,
                                             "properties": {f"{p} Prio": f"{p} Hoch"}}])["objects"][0]["uuid"]
    assert tool("create_objects", objects=[{"tags": [f"{p} Andere"], "title": "zeigt", "target": page,
                                            "properties": {f"{p} Bezug": [other]}}])["verified"]
    assert "none of the tags" in tool.error("create_objects", objects=[
        {"tags": [f"{p} Andere"], "title": "falsch", "target": page, "properties": {f"{p} Bezug": [model["target"]]}}])


def test_create_property_refusals(tool, model):
    p = model["p"]
    assert "already exists" in tool.error("create_property", title=f"{p} text")
    assert "a choice needs" in tool.error("create_property", title=f"{p} X1", choices=[f"{p} Ziel"])
    assert "only apply to node" in tool.error("create_property", title=f"{p} X2", classes=[f"{p} Andere"])
    assert "text properties" in tool.error("create_property", title=f"{p} X3", type="number", choices=["a"])


def test_update_property(tool, model):
    p = model["p"]
    tool("create_property", title=f"{p} Alt", type="node", classes=[f"{p} Andere"])
    r = tool("update_property", property=f"{p} Alt", title=f"{p} Neu", classes=[f"{p} Basis", f"{p} Andere"])
    assert r["verified"], r["problems"]
    props = schema(tool)[1]
    assert f"{p} Alt" not in props and sorted(props[f"{p} Neu"]["classes"]) == sorted([f"{p} Basis", f"{p} Andere"])
    assert tool("update_property", property=f"{p} Neu", classes=[])["verified"]
    assert "classes" not in schema(tool)[1][f"{p} Neu"]

    tool("create_property", title=f"{p} Stufe", choices=[f"{p} Eins"])
    assert tool("update_property", property=f"{p} Stufe", add_choices=[f"{p} Zwei"])["verified"]
    assert sorted(schema(tool)[1][f"{p} Stufe"]["choices"]) == [f"{p} Eins", f"{p} Zwei"]
    assert "nothing to change" in tool.error("update_property", property=f"{p} Stufe")
    assert "built-in" in tool.error("update_property", property="Status", title="x")


def test_update_tag(tool, model):
    p = model["p"]
    before = schema(tool)[0][f"{p} Andere"]["properties"]
    plan = tool("update_tag", tag=f"{p} Andere", add_properties=[f"{p} number"], dry_run=True)
    assert plan["changes"]["properties"]["new"] == [*before, f"{p} number"]
    assert schema(tool)[0][f"{p} Andere"]["properties"] == before

    assert tool("update_tag", tag=f"{p} Andere", add_properties=[f"{p} number", f"{p} text"])["verified"]
    assert set(schema(tool)[0][f"{p} Andere"]["properties"]) >= {f"{p} number", f"{p} text"}
    assert tool("update_tag", tag=f"{p} Andere", remove_properties=[f"{p} number"])["verified"]
    props = schema(tool)[0][f"{p} Andere"]["properties"]
    assert f"{p} number" not in props and f"{p} text" in props
    assert tool("update_tag", tag=f"{p} Andere", properties=[f"{p} text"])["verified"]
    assert schema(tool)[0][f"{p} Andere"]["properties"] == [f"{p} text"]
    assert "is not a class property" in tool.error("update_tag", tag=f"{p} Andere", remove_properties=[f"{p} url"])

    # inheritance: Andere extends Basis, then not; a cycle is refused
    assert tool("update_tag", tag=f"{p} Andere", add_extends=[f"{p} Basis"])["verified"]
    tags = schema(tool)[0]
    assert f"{p} Basis" in tags[f"{p} Andere"]["extends"] and tags[f"{p} Andere"]["inherited_properties"]
    assert "cycle" in tool.error("update_tag", tag=f"{p} Basis", add_extends=[f"{p} Andere"])
    assert tool("update_tag", tag=f"{p} Andere", remove_extends=[f"{p} Basis"])["verified"]
    assert f"{p} Basis" not in schema(tool)[0][f"{p} Andere"]["extends"]
    assert "built-in" in tool.error("update_tag", tag="Page", add_properties=[f"{p} text"])


def test_update_tag_on_ui_tag(tool, model):
    """The case that matters for ReqSeq: a tag created in the UI (user.class)."""
    if not tool.ctx.graph.entities_by_title("Spike UI Tag"):
        pytest.skip("SpikeTest has no 'Spike UI Tag'")
    p = model["p"]
    tool("create_property", title=f"{p} UI-Feld", tags=["Spike UI Tag"])
    tags = schema(tool)[0]
    assert f"{p} UI-Feld" in tags["Spike UI Tag"]["properties"]
    assert tool("update_tag", tag="Spike UI Tag", remove_properties=[f"{p} UI-Feld"])["verified"]
    assert f"{p} UI-Feld" not in schema(tool)[0]["Spike UI Tag"]["properties"]


def test_schema_tools_refuse_with_access_rules(model):
    limited = Runner(make_settings(write_pages=(f"{model['p']} Ziel",)))
    assert "schema changes are disabled" in limited.error("create_property", title=f"{model['p']} Verboten")


def test_create_and_rename_tag(tool, model):
    p = model["p"]
    plan = tool("create_tag", title=f"{p} Neuer Tag", extends=[f"{p} Basis"], properties=[f"{p} text", f"{p} number"],
                dry_run=True)
    assert plan["dry_run"] and f"{p} Neuer Tag" not in schema(tool)[0]

    r = tool("create_tag", title=f"{p} Neuer Tag", extends=[f"{p} Basis"], properties=[f"{p} text", f"{p} number"])
    assert r["verified"], r["problems"]
    assert r["tag"]["ident"].startswith(":plugin.class.")
    t = schema(tool)[0][f"{p} Neuer Tag"]
    # Logseq keeps class properties as a set: the order given is not kept (API-BEFUNDE I21)
    assert t["extends"] == [f"{p} Basis"] and sorted(t["properties"]) == sorted([f"{p} text", f"{p} number"])
    assert t["inherited_properties"]  # Basis' class property

    page = tool("create_page", title=f"{p} Neue Tag-Objekte")["page"]["uuid"]
    obj = tool("create_objects", objects=[{"tags": [f"{p} Neuer Tag"], "title": "mit neuem Tag", "target": page,
                                           "properties": {f"{p} number": 1}}])["objects"][0]["uuid"]
    assert any(o["uuid"] == obj for o in tool("find_objects", tag=f"{p} Basis")["objects"])  # via the subtag

    assert tool("update_tag", tag=f"{p} Neuer Tag", title=f"{p} Umbenannter Tag")["verified"]
    tags = schema(tool)[0]
    assert f"{p} Umbenannter Tag" in tags and f"{p} Neuer Tag" not in tags
    assert [x["title"] for x in tool("get_node", uuid=obj, depth=0)["tags"]] == [f"{p} Umbenannter Tag"]

    assert "already exists" in tool.error("create_tag", title=f"{p} Basis")
    assert "already exists" in tool.error("update_tag", tag=f"{p} Umbenannter Tag", title=f"{p} Andere")
    assert "cannot be extended" in tool.error("create_tag", title=f"{p} X4", extends=["Page"])


def test_rename_ui_tag_and_back(tool, model):
    if not tool.ctx.graph.entities_by_title("Spike UI Tag"):
        pytest.skip("SpikeTest has no 'Spike UI Tag'")
    p = model["p"]
    assert tool("update_tag", tag="Spike UI Tag", title=f"{p} UI Tag umbenannt")["verified"]
    assert f"{p} UI Tag umbenannt" in schema(tool)[0]
    assert tool("update_tag", tag=f"{p} UI Tag umbenannt", title="Spike UI Tag")["verified"]
    assert "Spike UI Tag" in schema(tool)[0]
