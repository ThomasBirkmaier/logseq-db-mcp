"""End-to-end: every tool against SpikeTest (see conftest.py)."""

import pytest

pytestmark = pytest.mark.live


def titles(node):
    return [c["title"] for c in node.get("children", [])]


def test_schema_lists_test_model(tool, model):
    s = tool("get_schema")
    tags = {t["title"]: t for t in s["tags"]}
    assert tags[f"{model['p']} Unter"]["extends"] == [f"{model['p']} Basis"]
    assert tags[f"{model['p']} Unter"]["inherited_properties"]
    props = {p["title"]: p for p in s["properties"]}
    assert props[f"{model['p']} refs"]["cardinality"] == "many"
    assert props[f"{model['p']} when"]["type"] == "datetime"
    assert sorted(props[f"{model['p']} choice"]["choices"]) == sorted([model["gruen"], model["rot"]])


def test_create_page_with_typed_values_and_outline(tool, model):
    p = model["p"]
    values = {
        f"{p} text": "Hallo", f"{p} number": 7.5, f"{p} check": True, f"{p} url": "https://example.org",
        f"{p} date": "2031-02-03", f"{p} when": "2031-02-03T10:30:00Z", f"{p} ref": model["target"],
        f"{p} refs": [model["target"], f"{p} Zweitziel"], f"{p} choice": model["rot"],
    }
    r = tool("create_page", title=f"{p} Seite", tags=[f"{p} Andere"], properties=values,
             blocks=[{"title": "eins", "children": [{"title": "eins.a"}, {"title": "eins.b"}]}, {"title": "zwei"}])
    assert r["verified"], r["problems"]
    node = tool("get_node", uuid=r["page"]["uuid"], depth=2)
    assert titles(node) == ["eins", "zwei"]
    assert titles(node["children"][0]) == ["eins.a", "eins.b"]
    got = node["properties"]
    assert got[f"{p} text"] == "Hallo" and got[f"{p} number"] == 7.5 and got[f"{p} check"] is True
    assert got[f"{p} date"] == "2031-02-03" and got[f"{p} when"] == "2031-02-03T10:30:00Z"
    assert got[f"{p} ref"]["uuid"] == model["target"]
    assert {x["title"] for x in got[f"{p} refs"]} == {f"{p} Ziel", f"{p} Zweitziel"}
    assert got[f"{p} choice"] == model["rot"]
    assert [t["title"] for t in node["tags"]] == [f"{p} Andere"]
    # no stray empty block from createPage
    assert "" not in titles(node)


def test_create_page_refuses_existing_title(tool, model):
    assert "already exists" in tool.error("create_page", title=f"{model['p']} Ziel")


def test_insert_positions(tool, model, run_id):
    page = tool("create_page", title=f"{model['p']} Positionen", blocks=[{"title": "B"}])["page"]["uuid"]
    b = tool("get_node", uuid=page, depth=1)["children"][0]["uuid"]
    tool("insert_blocks", target=page, position="first_child", blocks=[{"title": "A"}])
    tool("insert_blocks", target=page, position="last_child", blocks=[{"title": "D"}])
    tool("insert_blocks", target=b, position="after", blocks=[{"title": "C"}])
    tool("insert_blocks", target=b, position="before", blocks=[{"title": "A2"}])
    tool("insert_blocks", target=b, position="last_child", blocks=[{"title": "B1"}, {"title": "B2"}])
    tool("insert_blocks", target=b, position="first_child", blocks=[{"title": "B0"}])
    node = tool("get_node", uuid=page, depth=2)
    assert titles(node) == ["A", "A2", "B", "C", "D"]
    assert titles(node["children"][2]) == ["B0", "B1", "B2"]


def test_insert_dry_run_writes_nothing(tool, model):
    before = tool("get_node", uuid=model["target"], depth=1)
    r = tool("insert_blocks", target=model["target"], blocks=[{"title": "nur Plan"}], dry_run=True)
    assert r["dry_run"]
    assert tool("get_node", uuid=model["target"], depth=1) == before


def test_links(tool, model):
    page = tool("create_page", title=f"{model['p']} Links")["page"]["uuid"]
    err = tool.error("insert_blocks", target=page, blocks=[{"title": "siehe [[Gibt es nicht 4711]]"}])
    assert "does not exist" in err
    r = tool("insert_blocks", target=page, blocks=[{"title": f"siehe [[{model['p'].lower()} ziel]]"}])
    assert r["verified"], r["problems"]
    back = tool("get_backlinks", uuid=model["target"])
    assert any(b["uuid"] == r["blocks"][0]["uuid"] for b in back["backlinks"])


def test_objects_find_and_update(tool, model):
    p = model["p"]
    page = tool("create_page", title=f"{p} Objekte")["page"]["uuid"]
    r = tool("create_objects", objects=[
        {"tags": [f"{p} Unter"], "title": "Objekt 1", "target": page,
         "properties": {f"{p} number": 3, f"{p} choice": model["rot"], f"{p} text": "alpha"}},
        {"tags": [f"{p} Basis"], "title": "Objekt 2", "target": page,
         "properties": {f"{p} number": 10, f"{p} choice": model["gruen"]}},
        {"tags": [f"{p} Unter"], "title": f"{p} Objektseite", "as_page": True,
         "properties": {f"{p} ref": model["target"]}},
    ])
    assert r["verified"], r["problems"]
    assert all(o["missing_class_properties"] for o in r["objects"])  # the inherited "Pflicht" property
    o1, o2, o3 = (o["uuid"] for o in r["objects"])

    found = tool("find_objects", tag=f"{p} Basis")
    assert {o["uuid"] for o in found["objects"]} == {o1, o2, o3}
    assert {o["uuid"] for o in tool("find_objects", tag=f"{p} Basis", include_subtags=False)["objects"]} == {o2}
    gt = tool("find_objects", tag=f"{p} Basis", where=[{"property": f"{p} number", "op": "gt", "value": 5}])
    assert [o["uuid"] for o in gt["objects"]] == [o2]
    rot = tool("find_objects", tag=f"{p} Basis", where=[{"property": f"{p} choice", "op": "eq", "value": "lt " + model["rot"][3:]}])
    assert [o["uuid"] for o in rot["objects"]] == [o1]
    ref = tool("find_objects", tag=f"{p} Basis", where=[{"property": f"{p} ref", "op": "eq", "value": model["target"]}])
    assert [o["uuid"] for o in ref["objects"]] == [o3]
    empty = tool("find_objects", tag=f"{p} Basis", where=[{"property": f"{p} text", "op": "empty"}])
    assert {o["uuid"] for o in empty["objects"]} == {o2, o3}
    by_number = tool("find_objects", tag=f"{p} Basis", sort_by=f"{p} number", descending=True)
    assert [o["uuid"] for o in by_number["objects"]][:2] == [o2, o1]

    u = tool("update_objects", updates=[
        {"uuid": o1, "title": "Objekt 1 neu", "properties": {f"{p} text": None, f"{p} number": 4}},
        {"uuid": o3, "properties": {f"{p} refs": [model["target"]]}},
    ])
    assert u["verified"], u["problems"]
    u = tool("update_objects", updates=[{"uuid": o3, "properties": {f"{p} refs": [f"{p} Zweitziel"]}}])
    assert u["verified"], u["problems"]  # many: replaced, not added
    n3 = tool("get_node", uuid=o3, depth=0)
    assert [x["title"] for x in n3["properties"][f"{p} refs"]] == [f"{p} Zweitziel"]
    n1 = tool("get_node", uuid=o1, depth=0)
    assert n1["title"] == "Objekt 1 neu" and f"{p} text" not in n1.get("properties", {})


def test_invalid_values(tool, model):
    p = model["p"]
    page = model["target"]
    assert "is a number" in tool.error("insert_blocks", target=page,
                                       blocks=[{"title": "x", "properties": {f"{p} number": "zehn"}}])
    assert "not a choice" in tool.error("insert_blocks", target=page,
                                        blocks=[{"title": "x", "properties": {f"{p} choice": "Blau"}}])
    assert "Unknown property" in tool.error("insert_blocks", target=page,
                                            blocks=[{"title": "x", "properties": {"Gibt es nicht 4711": 1}}])
    assert "takes one value" in tool.error("insert_blocks", target=page,
                                           blocks=[{"title": "x", "properties": {f"{p} text": ["a", "b"]}}])


def test_tags(tool, model):
    p = model["p"]
    page = tool("create_page", title=f"{p} Tags", blocks=[{"title": "wird getaggt"}])["page"]["uuid"]
    b = tool("get_node", uuid=page, depth=1)["children"][0]["uuid"]
    assert tool("tag_nodes", uuids=[b], tags=[f"{p} Andere"])["verified"]
    assert any(o["uuid"] == b for o in tool("find_objects", tag=f"{p} Andere")["objects"])
    assert tool("untag_nodes", uuids=[b], tags=[f"{p} Andere"])["verified"]
    assert "cannot be set as a tag" in tool.error("tag_nodes", uuids=[b], tags=["Page"])


def test_move(tool, model):
    page = tool("create_page", title=f"{model['p']} Verschieben",
                blocks=[{"title": t} for t in ["a", "b", "c", "d"]] + [{"title": "Ziel"}])["page"]["uuid"]
    kids = {k["title"]: k["uuid"] for k in tool("get_node", uuid=page, depth=1)["children"]}
    r = tool("move_blocks", uuids=[kids["c"], kids["d"]], target=kids["a"], position="before")
    assert r["verified"], r["problems"]
    assert titles(tool("get_node", uuid=page, depth=1)) == ["c", "d", "a", "b", "Ziel"]
    assert tool("move_blocks", uuids=[kids["a"], kids["b"]], target=kids["Ziel"], position="last_child")["verified"]
    assert tool("move_blocks", uuids=[kids["c"]], target=kids["Ziel"], position="first_child")["verified"]
    assert tool("move_blocks", uuids=[kids["d"]], target=kids["Ziel"], position="after")["verified"]
    node = tool("get_node", uuid=page, depth=2)
    assert titles(node) == ["Ziel", "d"]
    assert titles(node["children"][0]) == ["c", "a", "b"]
    assert "itself" in tool.error("move_blocks", uuids=[kids["Ziel"]], target=kids["a"], position="last_child")
    assert tool("move_blocks", uuids=[kids["d"]], target=page, position="first_child")["verified"]
    assert titles(tool("get_node", uuid=page, depth=1)) == ["d", "Ziel"]


def test_update_and_rename(tool, model):
    p = model["p"]
    page = tool("create_page", title=f"{p} Umbenennen", blocks=[{"title": "alt"}])["page"]["uuid"]
    b = tool("get_node", uuid=page, depth=1)["children"][0]["uuid"]
    assert tool("update_block", uuid=b, title="neu")["verified"]
    assert tool("rename_page", uuid=page, title=f"{p} Umbenannt")["verified"]
    assert tool("get_node", uuid=page, depth=0)["title"] == f"{p} Umbenannt"
    assert "already exists" in tool.error("rename_page", uuid=page, title=f"{p} Ziel")
    assert "rename_page" in tool.error("update_block", uuid=page, title="x")


def test_journal(tool, model):
    r = tool("add_to_journal", date="2031-03-04", blocks=[{"title": f"{model['p']} Eintrag"}])
    assert r["verified"], r["problems"]
    j = tool("get_journal", date="2031-03-04")
    assert j["date"] == "2031-03-04" and f"{model['p']} Eintrag" in titles(j)
    assert tool("get_journal", date="1999-01-01") == {"date": "1999-01-01", "exists": False}


def test_resolve_and_search(tool, model):
    m = tool("resolve", title=f"{model['p']} ziel")
    assert m["matches"][0]["uuid"] == model["target"] and m["exact"] == 1
    tool("insert_blocks", target=model["target"], blocks=[{"title": "Träger", "properties": {f"{model['p']} text": "Suchwort7392"}}])
    hits = tool("search", text="suchwort7392")
    assert any(h["title"] == "Träger" for h in hits["results"])


def test_delete(tool, model):
    page = tool("create_page", title=f"{model['p']} Löschen", blocks=[{"title": "weg", "children": [{"title": "auch weg"}]}, {"title": "bleibt"}])["page"]["uuid"]
    b = tool("get_node", uuid=page, depth=1)["children"][0]["uuid"]
    plan = tool("delete_nodes", uuids=[b])
    assert plan["confirm_needed"] and plan["would_delete"][0]["children"] == 1
    assert tool("delete_nodes", uuids=[b], confirm=True)["verified"]
    assert titles(tool("get_node", uuid=page, depth=1)) == ["bleibt"]
    assert "Logseq UI" in tool.error("delete_nodes", uuids=[model["base"]], confirm=True)


def test_call_api_and_query(tool, model):
    r = tool("call_api", method="logseq.App.getCurrentGraph")
    assert r["result"]["name"] == "logseq_db_SpikeTest"
    assert "blocked" in tool.error("call_api", method="logseq.App.quit")
    q = tool("query", query="[:find ?t :in $ ?u :where [?e :block/uuid ?u] [?e :block/title ?t]]",
             inputs=[f'#uuid "{model["target"]}"'])
    assert q["rows"] == [[f"{model['p']} Ziel"]]


def test_access_rules(settings, model):
    from .conftest import Runner, make_settings

    p = model["p"]
    hidden = Runner(make_settings(hide_tags=(f"{p} Andere",)))
    found = hidden("find_objects", tag=f"{p} Basis")
    assert found["total"] >= 1
    seite = hidden("resolve", title=f"{p} Seite")["matches"]
    assert seite == []  # tagged "Andere": hidden
    # blocks on a hidden page are hidden too
    visible = Runner(make_settings())
    page = visible("resolve", title=f"{p} Seite")["matches"][0]["uuid"]
    block = visible("get_node", uuid=page, depth=1)["children"][0]["uuid"]
    assert "hidden" in hidden.error("get_node", uuid=block)
    assert all(r["page"]["uuid"] != page for r in hidden("search", text="eins.a")["results"])
    limited = Runner(make_settings(write_pages=(f"{p} Ziel",)))
    assert limited("insert_blocks", target=model["target"], blocks=[{"title": "erlaubt"}])["verified"]
    other = limited("resolve", title=f"{p} Objekte")["matches"][0]["uuid"]
    assert "read-only" in limited.error("insert_blocks", target=other, blocks=[{"title": "verboten"}])
    assert "call_api" in limited.error("call_api", method="logseq.App.getCurrentGraph")
    ro = Runner(make_settings())
    ro.tools = {k: v for k, v in ro.tools.items() if not v.writes}
    assert "Unknown tool" in ro.error("create_page", title="x")


def test_deleted_pages_go_to_recycle_bin_and_disappear(tool, model):
    p = model["p"]
    page = tool("create_objects", objects=[{"tags": [f"{p} Andere"], "title": f"{p} Papierkorb", "as_page": True,
                                            "properties": {f"{p} ref": model["target"]}}])["objects"][0]["uuid"]
    assert any(b["uuid"] == page for b in tool("get_backlinks", uuid=model["target"])["backlinks"])
    assert tool("delete_nodes", uuids=[page], confirm=True)["verified"]
    assert all(o["uuid"] != page for o in tool("find_objects", tag=f"{p} Andere")["objects"])
    assert all(b["uuid"] != page for b in tool("get_backlinks", uuid=model["target"])["backlinks"])
    assert tool("resolve", title=f"{p} Papierkorb")["matches"] == []
    assert tool("get_node", uuid=page, depth=0)["recycled"] is True
    assert "recycle bin" in tool.error("create_page", title=f"{p} Papierkorb")
