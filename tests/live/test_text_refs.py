"""API-BEFUNDE I10: node references stored as text, as found in ReqSeq, and their repair.

Reproduces the ReqSeq shape in SpikeTest: a node property restricted to a
status tag, values written as UUID strings without ``entityId``. Then
update_objects writes the same value again and must leave a real reference.
"""

import pytest

pytestmark = pytest.mark.live


def raw_value(tool, uuid, ident):
    rows = tool.ctx.client.q(f'[:find (pull ?b [*]) :where [?b :block/uuid #uuid "{uuid}"]]')
    return rows[0][0].get(ident)


def pull_id(tool, eid):
    rows = tool.ctx.client.q("[:find (pull ?e [*]) :in $ ?e]", str(eid))
    e = rows[0][0] if rows else None
    return e if e and "uuid" in e else None  # a deleted entity still pulls as {"id": N}


def test_reproduce_and_repair_text_reference(tool, run_id):
    c = tool.ctx.client
    p = f"RS {run_id}"
    status_tag = c.call("logseq.Editor.createTag", f"{p} Status")
    c.call("logseq.Editor.createTag", f"{p} Objekt")
    def add_property(classes):
        data = {"title": f"{p} Zustand", "property-type": "node", "property-cardinality": "one"}
        if classes:
            data["property-classes"] = classes
        c.mcp_tool("upsertNodes", {"operations": [{"operation": "add", "entityType": "property", "id": None,
                                                   "data": data}]})

    # property-classes with an API (plugin) class fails by UUID; try the name, then none
    for classes in ([status_tag["uuid"]], [f"{p} Status"], None):
        try:
            add_property(classes)
            break
        except Exception as e:
            print(f"upsertNodes property-classes={classes}: {e}")
    tool.ctx.schema.invalidate()
    prop = tool.ctx.schema.property(f"{p} Zustand")
    print(f"property classes: {prop.classes}")
    assert prop.type == "node"

    statuses = tool("create_objects", objects=[
        {"tags": [f"{p} Status"], "title": f"{p} Draft", "as_page": True},
        {"tags": [f"{p} Status"], "title": f"{p} Approved", "as_page": True}])["objects"]
    draft, approved = statuses[0]["uuid"], statuses[1]["uuid"]
    obj = tool("create_objects", objects=[{"tags": [f"{p} Objekt"], "title": f"{p} Need", "as_page": True}])["objects"][0]["uuid"]

    # 1. the faulty write: UUID string, no entityId (what produced ReqSeq's values)
    c.call("logseq.Editor.upsertBlockProperty", obj, prop.ident, draft)
    raw = raw_value(tool, obj, prop.ident)
    vb = pull_id(tool, raw["id"])
    assert ":logseq.property/created-from-property" in vb and vb["title"] == draft, "I10 not reproduced"
    old_value_block = raw["id"]

    # 2. the server reads it as a reference marked stored_as_text; no backlink exists
    node = tool("get_node", uuid=obj, depth=0)
    assert node["properties"][f"{p} Zustand"] == {"uuid": draft, "title": f"{p} Draft", "stored_as_text": True}
    assert all(b["uuid"] != obj for b in tool("get_backlinks", uuid=draft)["backlinks"])
    found = tool("find_objects", tag=f"{p} Objekt", where=[{"property": f"{p} Zustand", "op": "eq", "value": f"{p} Draft"}])
    assert [o["uuid"] for o in found["objects"]] == [obj]

    # 3. repair: write the same value through the server
    plan = tool("update_objects", updates=[{"uuid": obj, "properties": {f"{p} Zustand": draft}}], dry_run=True)
    assert plan["dry_run"]
    r = tool("update_objects", updates=[{"uuid": obj, "properties": {f"{p} Zustand": draft}}])
    assert r["verified"], r["problems"]
    raw = raw_value(tool, obj, prop.ident)
    assert raw == {"id": pull_id_by_uuid(tool, draft)}, f"still not a reference: {raw}"
    node = tool("get_node", uuid=obj, depth=0)
    assert node["properties"][f"{p} Zustand"] == {"uuid": draft, "title": f"{p} Draft"}
    assert any(b["uuid"] == obj for b in tool("get_backlinks", uuid=draft)["backlinks"])
    assert pull_id(tool, old_value_block) is None, "orphaned value block left behind"

    # 4. changing it afterwards keeps it a reference
    assert tool("update_objects", updates=[{"uuid": obj, "properties": {f"{p} Zustand": f"{p} Approved"}}])["verified"]
    assert raw_value(tool, obj, prop.ident) == {"id": pull_id_by_uuid(tool, approved)}


def pull_id_by_uuid(tool, uuid):
    return tool.ctx.graph.require(uuid)["id"]
