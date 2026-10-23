import pytest

from logseq_db_mcp import values
from logseq_db_mcp.schema import Property
from logseq_db_mcp.values import InvalidValue

TARGET = {"id": 10, "uuid": "11111111-1111-4111-8111-111111111111", "title": "Ziel", "tags": [{"id": 4}]}
OTHER = {"id": 11, "uuid": "22222222-2222-4222-8222-222222222222", "title": "Andere", "tags": [{"id": 7}]}
JOURNAL = {"id": 20, "uuid": "00000001-2031-0203-0000-000000000000", "title": "Feb 3rd, 2031", "journal-day": 20310203}


def prop(type_="default", many=False, choices=None, classes=None):
    return Property(id=1, uuid="u", ident=":user.property/x", title="X", type=type_, many=many,
                    builtin=False, hidden=False, classes=classes or [], choices=choices or {})


class Resolver:
    def __init__(self):
        self.created_journals = []

    def entity_by_uuid(self, uuid):
        return {e["uuid"]: e for e in (TARGET, OTHER)}.get(uuid)

    def entities_by_title(self, title):
        return [e for e in (TARGET, OTHER) if e["title"].lower() == title.lower()]

    def journal(self, iso, create):
        if create:
            self.created_journals.append(iso)
            return JOURNAL
        return None

    def tag_ids(self, e):
        return {t["id"] for t in e.get("tags", [])}

    def allowed_tag_ids(self, class_ids):
        return set(class_ids)


@pytest.mark.parametrize("type_, value, raw, expected", [
    ("default", "Hallo", "Hallo", "Hallo"),
    ("number", 7.5, 7.5, 7.5),
    ("checkbox", False, False, False),
    ("url", "https://x.org", "https://x.org", "https://x.org"),
    ("datetime", "2031-02-03T10:30:00Z", 1927881000000, "2031-02-03T10:30:00Z"),
    ("datetime", "2031-02-03T11:30:00+01:00", 1927881000000, "2031-02-03T10:30:00Z"),
    ("datetime", 1927881000000, 1927881000000, "2031-02-03T10:30:00Z"),
])
def test_encode_literals(type_, value, raw, expected):
    enc = values.encode(prop(type_), value, Resolver())
    assert enc.value == raw and enc.expected == expected and enc.opts == {}


def test_encode_date_creates_journal():
    r = Resolver()
    enc = values.encode(prop("date"), "2031-02-03", r)
    assert (enc.value, enc.opts, enc.expected) == (20, {"entityId": True}, "2031-02-03")
    assert r.created_journals == ["2031-02-03"]
    with pytest.raises(InvalidValue, match="no journal"):
        values.encode(prop("date"), "2031-02-03", r, create_journals=False)


def test_encode_node_by_uuid_or_title_and_classes():
    enc = values.encode(prop("node"), TARGET["uuid"], Resolver())
    assert enc.value == 10 and enc.opts == {"entityId": True}
    assert values.encode(prop("node"), "ziel", Resolver()).expected["uuid"] == TARGET["uuid"]
    with pytest.raises(InvalidValue, match="none of the tags"):
        values.encode(prop("node", classes=[4]), "Andere", Resolver())
    with pytest.raises(InvalidValue, match="no page or tag"):
        values.encode(prop("node"), "Gibt es nicht", Resolver())


def test_encode_many_and_choices():
    enc = values.encode(prop("node", many=True), ["Ziel", "Andere"], Resolver())
    assert enc.value == [10, 11]
    assert values.encode(prop(many=True), "a", Resolver()).value == ["a"]
    with pytest.raises(InvalidValue, match="takes one value"):
        values.encode(prop(), ["a"], Resolver())
    choice = prop(choices={30: "Rot", 31: "Grün"})
    assert values.encode(choice, "rot", Resolver()).value == 30
    with pytest.raises(InvalidValue, match="not a choice"):
        values.encode(choice, "Blau", Resolver())


@pytest.mark.parametrize("type_, value", [("number", "7"), ("number", True), ("checkbox", "ja"),
                                          ("default", 3), ("url", "keine url"), ("datetime", "morgen"),
                                          ("date", "03.02.2031")])
def test_encode_rejects_wrong_types(type_, value):
    with pytest.raises(InvalidValue):
        values.encode(prop(type_), value, Resolver())


def test_decode():
    ents = {50: {"id": 50, ":logseq.property/created-from-property": {"id": 1}, "title": "Text im Wert-Block"},
            51: {"id": 51, ":logseq.property/created-from-property": {"id": 1}, ":logseq.property/value": 42},
            10: TARGET, 20: JOURNAL, 30: {"id": 30, "title": "Rot"}}
    assert values.decode(prop(), {"id": 50}, ents) == "Text im Wert-Block"
    assert values.decode(prop("number"), {"id": 51}, ents) == 42
    assert values.decode(prop("node"), {"id": 10}, ents) == {"uuid": TARGET["uuid"], "title": "Ziel"}
    assert values.decode(prop("date"), {"id": 20}, ents) == "2031-02-03"
    assert values.decode(prop("datetime"), 1927881000000, ents) == "2031-02-03T10:30:00Z"
    assert values.decode(prop(choices={30: "Rot"}), {"id": 30}, ents) == "Rot"
    assert values.decode(prop("node", many=True), [{"id": 10}], ents) == [{"uuid": TARGET["uuid"], "title": "Ziel"}]
    assert values.decode(prop(many=True), None, ents) == []
    assert values.decode(prop("node"), {"id": 99}, ents) == {"id": 99}
