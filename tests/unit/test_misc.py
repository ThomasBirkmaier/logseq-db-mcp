"""refs, find_objects filters, settings, call_api checks, writer helpers."""

import pytest

from logseq_db_mcp import refs
from logseq_db_mcp.settings import load_settings
from logseq_db_mcp.tools.raw import validate_method
from logseq_db_mcp.tools.read import matches
from logseq_db_mcp.writer import _same

U = "11111111-1111-4111-8111-111111111111"


def test_refs():
    text = f"siehe [[{U}]] und [[Neue Seite]] und [[Neue Seite]]"
    assert refs.uuids_in(text) == {U}
    assert refs.render(text, {U: "Ziel"}) == "siehe [[Ziel]] und [[Neue Seite]] und [[Neue Seite]]"
    assert refs.render(text, {}) == text
    assert refs.title_links(text) == ["Neue Seite"]
    assert refs.title_links(None) == []


REF = {"uuid": U, "title": "Ziel"}


@pytest.mark.parametrize("value, op, wanted, result", [
    ("Alpha", "eq", "alpha", True), ("Alpha", "ne", "alpha", False), ("Alpha", "contains", "lph", True),
    (None, "empty", None, True), ([], "empty", None, True), ("", "empty", None, True), ("x", "not_empty", None, True),
    (5, "gt", 3, True), (5, "lt", 3, False), (5, "gte", 5, True), ("2031-02-03", "lt", "2031-03-01", True),
    (REF, "eq", U, True), (REF, "eq", "ziel", True), ([REF], "contains", "zie", True),
    ({**REF, "id": "N-007"}, "eq", "n-007", True), (REF, "eq", "N-007", False),
    ([REF, {"uuid": "x", "title": "Y"}], "eq", "y", True), ("Alpha", "gt", 3, False),
])
def test_matches(value, op, wanted, result):
    assert matches(value, op, wanted) is result


def test_same():
    assert _same([{"uuid": "a"}, {"uuid": "b"}], [{"uuid": "b", "title": "B"}, {"uuid": "a"}])
    assert _same("Text", "text")
    assert not _same([1], [1, 2])
    assert _same(None, None)


def test_settings(monkeypatch):
    monkeypatch.delenv("LOGSEQ_API_TOKEN", raising=False)
    with pytest.raises(ValueError):
        load_settings()
    monkeypatch.setenv("LOGSEQ_API_TOKEN", "t")
    monkeypatch.setenv("LOGSEQ_API_URL", "http://localhost:12315/mcp")
    monkeypatch.setenv("LOGSEQ_HIDE_TAGS", "Privat, Geheim")
    monkeypatch.setenv("LOGSEQ_ENABLE_CALL_API", "true")
    monkeypatch.setenv("LOGSEQ_API_READ_TIMEOUT", "-1")
    s = load_settings()
    assert s.base_url == "http://127.0.0.1:12315"  # /mcp rejects Host: localhost; path dropped
    assert s.hide_tags == ("Privat", "Geheim") and s.has_access_rules
    assert s.enable_call_api and s.read_timeout == 15


@pytest.mark.parametrize("method", ["logseq.Git.execCommand", "logseq.App.quit", "logseq.App.openExternalLink",
                                    "Editor.getPage", "logseq.Editor.get-page", "logseq.Editor.getPage; rm"])
def test_call_api_rejects(method):
    with pytest.raises(ValueError):
        validate_method(method)


def test_call_api_allows():
    validate_method("logseq.Editor.getBlock")
    validate_method("logseq.DB.datascriptQuery")


def test_recycled_pages_are_hidden_from_lists_but_marked_by_uuid():
    from logseq_db_mcp.graph import DELETED_AT, Graph
    from logseq_db_mcp.schema import Schema, Tag

    from .conftest import FakeClient, settings

    page_class = Tag(id=4, uuid="p", ident=":logseq.class/Page", title="Page", builtin=True)

    class Cache:
        def get(self):
            return Schema([], [page_class])

    g = Graph(FakeClient(lambda m, a: []), Cache(), settings())
    live = {"id": 1, "uuid": U, "title": "Seite", "tags": [{"id": 4}]}
    gone = {"id": 2, "uuid": "22222222-2222-4222-8222-222222222222", "title": "Weg", "tags": [{"id": 4}],
            DELETED_AT: 1792776640041}
    block_on_gone = {"id": 3, "uuid": "33333333-3333-4333-8333-333333333333", "title": "b",
                     "page": {"id": 2}, "parent": {"id": 2}}
    assert [n["uuid"] for n in g.serialize([live, gone, block_on_gone])] == [U]
    marked = g.serialize([gone], include_recycled=True)
    assert marked[0]["recycled"] is True
