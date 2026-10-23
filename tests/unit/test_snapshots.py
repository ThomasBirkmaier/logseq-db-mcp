"""Tools against recorded real answers from SpikeTest: the output must not drift."""

import json

import pytest

from logseq_db_mcp.server import dispatch, registry
from logseq_db_mcp.tools.base import Context

from .conftest import ReplayClient, settings


def _cases():
    from .conftest import FIXTURES
    data = json.loads((FIXTURES / "spiketest.json").read_text())
    return [pytest.param(c, id=f"{c['tool']}-{i}") for i, c in enumerate(data["cases"])]


@pytest.mark.parametrize("case", _cases())
def test_tool_output_matches_recording(case):
    s = settings()
    ctx = Context.create(s, ReplayClient(case["calls"], s))
    r = dispatch(ctx, registry(s, read_only=True), case["tool"], case["args"])
    assert not r.is_error, r.content[0].text
    assert json.loads(r.content[0].text) == case["output"]


def test_recording_covers_typed_values(recording):
    """The recorded page carries every property type the server converts."""
    p = recording["prefix"]
    node = next(c for c in recording["cases"] if c["tool"] == "get_node")["output"]
    props = node["properties"]
    assert props[f"{p} number"] == 7.5
    assert props[f"{p} check"] is True
    assert props[f"{p} date"] == "2031-02-03"
    assert props[f"{p} when"] == "2031-02-03T10:30:00Z"
    assert set(props[f"{p} ref"]) == {"uuid", "title"}
    assert isinstance(props[f"{p} refs"], list) and len(props[f"{p} refs"]) == 2
    assert props[f"{p} choice"].endswith("Rot")
    # value blocks never appear as outline children
    assert [c["title"] for c in node["children"]] == ["eins", "zwei"]
