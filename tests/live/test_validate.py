"""validate_objects and references by ID, end-to-end in SpikeTest."""

import pytest

from .conftest import Runner, make_settings

pytestmark = pytest.mark.live


@pytest.fixture(scope="module")
def vt(tool, model):
    """A runner whose ID property is the test model's 'ID' property."""
    p = model["p"]
    tool.ctx.client.mcp_tool("upsertNodes", {"operations": [{"operation": "add", "entityType": "property", "id": None,
                                                             "data": {"title": f"{p} ID", "property-type": "default",
                                                                      "property-cardinality": "one"}}]})
    return Runner(make_settings(id_property=f"{p} ID"))


def test_references_by_id(vt, model):
    p = model["p"]
    page = vt("create_page", title=f"{p} IDs")["page"]["uuid"]
    target = vt("create_objects", objects=[{"tags": [f"{p} Andere"], "title": f"{p} Ziel mit ID", "target": page,
                                            "properties": {f"{p} ID": "X-9"}}])["objects"][0]["uuid"]
    r = vt("create_objects", objects=[{"tags": [f"{p} Andere"], "title": "verweist per ID", "target": page,
                                       "properties": {f"{p} refs": ["X-9"]}}])
    assert r["verified"], r["problems"]
    holder = r["objects"][0]["uuid"]
    got = vt("get_node", uuid=holder, depth=0)["properties"][f"{p} refs"]
    assert got == [{"uuid": target, "title": f"{p} Ziel mit ID", "id": "X-9"}]
    found = vt("find_objects", tag=f"{p} Andere", where=[{"property": f"{p} refs", "op": "eq", "value": "x-9"}])
    assert [o["uuid"] for o in found["objects"]] == [holder]


def test_validate_objects(vt, model):
    p = model["p"]
    c = vt.ctx.client
    page = vt("create_page", title=f"{p} Prüfung")["page"]["uuid"]
    gone = vt("create_page", title=f"{p} Bald im Papierkorb")["page"]["uuid"]
    objs = vt("create_objects", objects=[
        {"tags": [f"{p} Unter"], "title": "Doppelt A", "target": page, "properties": {f"{p} ID": "V-1"}},
        {"tags": [f"{p} Basis"], "title": "Doppelt B", "target": page, "properties": {f"{p} ID": "V-1"}},
        {"tags": [f"{p} Basis"], "title": "Zeigt in den Papierkorb", "target": page,
         "properties": {f"{p} ref": gone}},
        {"tags": [f"{p} Basis"], "title": "Verweis als Text", "target": page},
    ])["objects"]
    a, b, recycled_ref, text_ref = (o["uuid"] for o in objs)
    vt("delete_nodes", uuids=[gone], confirm=True)
    prop = vt.ctx.schema.property(f"{p} ref")
    c.call("logseq.Editor.upsertBlockProperty", text_ref, prop.ident, model["target"])  # the ReqSeq fault

    r = vt("validate_objects", tag=f"{p} Basis")
    by = {}
    for i in r["issues"]:
        by.setdefault(i["uuid"], set()).add(i["issue"])
    assert "missing" in by[a] and "missing" in by[b]  # the class property "Pflicht"
    assert "duplicate" in by[a] and "duplicate" in by[b]
    assert "recycled_target" in by[recycled_ref]
    assert "text_reference" in by[text_ref]
    assert r["summary"]["duplicate"] >= 2 and r["legend"]["duplicate"]
    relaxed = vt("validate_objects", tag=f"{p} Basis", optional=[f"lt{model['p'][3:]}Pflicht"])
    assert "missing" not in relaxed["summary"]
    only = vt("validate_objects", tag=f"{p} Basis", checks=["text_reference"])
    assert {i["issue"] for i in only["issues"]} == {"text_reference"}

    # the hint is right: update_objects with the same value repairs it
    vt("update_objects", updates=[{"uuid": text_ref, "properties": {f"{p} ref": model["target"]}}])
    after = vt("validate_objects", tag=f"{p} Basis", checks=["text_reference"])
    assert all(i["uuid"] != text_ref for i in after["issues"])
