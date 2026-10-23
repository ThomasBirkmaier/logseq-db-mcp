"""validate_objects checks that cannot be set up through the API (allowed tags, I11)."""

from logseq_db_mcp.schema import Property
from logseq_db_mcp.tools.validate import ISSUES, ValidateObjects

ALLOWED_TAG = 7
GOOD = {"id": 10, "uuid": "u-good", "title": "Need", "tags": [{"id": ALLOWED_TAG}]}
BAD = {"id": 11, "uuid": "u-bad", "title": "Decision", "tags": [{"id": 8}]}
TEXT = {"id": 12, "uuid": "u-text", "title": "nur Text", ":logseq.property/created-from-property": {"id": 1}}


class G:
    def allowed_tag_ids(self, classes):
        return set(classes)

    def tag_ids(self, e):
        return {t["id"] for t in e.get("tags", [])}


def check(p, raw, ents):
    issues = []
    ValidateObjects._check_value({"uuid": "o", "title": "O"}, p, raw, ents, {}, G(), set(ISSUES),
                                 lambda o, kind, prop, detail: issues.append((kind, detail)))
    return issues


def prop(type_="node", classes=(ALLOWED_TAG,), choices=None):
    return Property(id=1, uuid="p", ident=":user.property/x", title="X", type=type_, many=True, builtin=False,
                    hidden=False, classes=list(classes), choices=choices or {})


def test_reference_checks():
    ents = {10: GOOD, 11: BAD, 12: TEXT}
    assert check(prop(), [{"id": 10}], ents) == []
    assert [k for k, _ in check(prop(), [{"id": 11}], ents)] == ["disallowed_tag"]
    assert [k for k, _ in check(prop(), [{"id": 99}], ents)] == ["dangling"]
    assert [k for k, _ in check(prop(), [{"id": 12}], ents)] == ["wrong_type"]  # text, not a UUID
    assert [k for k, _ in check(prop(), "N-007", ents)] == ["wrong_type"]


def test_literal_checks():
    assert [k for k, _ in check(prop("checkbox", ()), "ja", {})] == ["wrong_type"]
    assert check(prop("checkbox", ()), True, {}) == []
    assert [k for k, _ in check(prop("datetime", ()), "morgen", {})] == ["wrong_type"]
    num = {20: {"id": 20, ":logseq.property/created-from-property": {"id": 1}, ":logseq.property/value": "zehn",
                "title": "zehn"}}
    assert [k for k, _ in check(prop("number", ()), {"id": 20}, num)] == ["wrong_type"]
    choice = prop("default", (), {30: "Rot"})
    assert check(choice, {"id": 30}, {30: {"id": 30}}) == []
    assert [k for k, _ in check(choice, {"id": 31}, {31: {"id": 31, "title": "Blau"}})] == ["wrong_type"]
