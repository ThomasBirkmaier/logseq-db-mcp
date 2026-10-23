"""Property values: from the graph to JSON (decode) and from JSON to an
``upsertBlockProperty`` call (encode).

How Logseq stores each type was measured in the spike (docs/API-BEFUNDE.md):

- default (text), number, url: a *value block*, an entity of its own with
  ``:logseq.property/created-from-property``; text in ``title``, numbers in
  ``:logseq.property/value``.
- checkbox: a literal.
- datetime: a literal, epoch milliseconds. ISO strings are rejected.
- date: a reference to the journal page of that day.
- node and other reference types: references; written as entity IDs with
  ``entityId: true``.
- closed values (choices): a reference to the choice entity.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any, Protocol

from .client import UUID_RE
from .schema import REF_TYPES, Property

VALUE_BLOCK = ":logseq.property/created-from-property"


class InvalidValue(ValueError):
    """A value does not fit its property."""


# --- decode ------------------------------------------------------------------


def ms_to_iso(ms: int | float) -> str:
    t = dt.datetime.fromtimestamp(ms / 1000, tz=dt.timezone.utc)
    return t.isoformat(timespec="seconds").replace("+00:00", "Z")


def day_to_iso(day: int) -> str:
    s = str(day)
    return f"{s[:4]}-{s[4:6]}-{s[6:8]}"


def decode(prop: Property, raw: Any, entities: dict[int, dict]) -> Any:
    """Raw pull value → JSON. ``entities`` holds the referenced entities by id."""
    items = raw if isinstance(raw, list) else [raw]
    out = [_decode_one(prop, x, entities) for x in items if x is not None]
    if prop.many:
        return out
    return out[0] if out else None


def _decode_one(prop: Property, x: Any, entities: dict[int, dict]) -> Any:
    if isinstance(x, dict) and "id" in x:
        e = entities.get(x["id"])
        if e is None:
            return {"id": x["id"]}
        if VALUE_BLOCK in e:
            return e.get(":logseq.property/value", e.get("title"))
        if x["id"] in prop.choices:
            return prop.choices[x["id"]]
        if prop.type == "date" and "journal-day" in e:
            return day_to_iso(e["journal-day"])
        return {"uuid": e.get("uuid"), "title": e.get("title")}
    if prop.type == "datetime" and isinstance(x, (int, float)) and not isinstance(x, bool):
        return ms_to_iso(x)
    return x


def referenced_ids(raw: Any) -> list[int]:
    items = raw if isinstance(raw, list) else [raw]
    return [x["id"] for x in items if isinstance(x, dict) and "id" in x]


# --- encode ------------------------------------------------------------------


class Resolver(Protocol):
    """What encoding needs from the graph."""

    def entity_by_uuid(self, uuid: str) -> dict | None: ...

    def entities_by_title(self, title: str) -> list[dict]: ...

    def journal(self, iso_date: str, create: bool) -> dict | None: ...

    def tag_ids(self, entity: dict) -> set[int]: ...

    def allowed_tag_ids(self, class_ids: list[int]) -> set[int]: ...


@dataclass
class Encoded:
    value: Any
    opts: dict
    expected: Any  # what decode() must return afterwards (read-back check)


def _iso_to_ms(v: Any) -> int:
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return int(v)
    if not isinstance(v, str):
        raise InvalidValue(f"expected an ISO date/time or epoch milliseconds, got {v!r}")
    s = v.strip().replace("Z", "+00:00")
    try:
        t = dt.datetime.fromisoformat(s)
    except ValueError:
        raise InvalidValue(f"not an ISO date/time: {v!r}") from None
    if t.tzinfo is None:
        t = t.replace(tzinfo=dt.timezone.utc)
    return int(t.timestamp() * 1000)


def _iso_date(v: Any) -> str:
    if not isinstance(v, str):
        raise InvalidValue(f"expected a date YYYY-MM-DD, got {v!r}")
    try:
        return dt.date.fromisoformat(v.strip()[:10]).isoformat()
    except ValueError:
        raise InvalidValue(f"not a date YYYY-MM-DD: {v!r}") from None


def encode(prop: Property, value: Any, r: Resolver, create_journals: bool = True) -> Encoded:
    """JSON value → arguments for upsertBlockProperty. ``None`` is handled by the caller (remove)."""
    if prop.many:
        items = value if isinstance(value, list) else [value]
        parts = [_encode_one(prop, v, r, create_journals) for v in items]
        return Encoded([p[0] for p in parts], parts[0][1] if parts else {}, [p[2] for p in parts])
    if isinstance(value, list):
        raise InvalidValue(f"'{prop.title}' takes one value, got a list")
    v, opts, expected = _encode_one(prop, value, r, create_journals)
    return Encoded(v, opts, expected)


def _encode_one(prop: Property, v: Any, r: Resolver, create_journals: bool) -> tuple[Any, dict, Any]:
    name = prop.title
    if prop.choices:
        wanted = v.get("title") if isinstance(v, dict) else v
        for cid, title in prop.choices.items():
            if isinstance(wanted, str) and title.lower() == wanted.lower():
                return cid, {"entityId": True}, title
        raise InvalidValue(f"'{wanted}' is not a choice of '{name}': {', '.join(prop.choices.values())}")
    t = prop.type
    if t == "checkbox":
        if not isinstance(v, bool):
            raise InvalidValue(f"'{name}' is a checkbox, expected true/false, got {v!r}")
        return v, {}, v
    if t == "number":
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raise InvalidValue(f"'{name}' is a number, got {v!r}")
        return v, {}, v
    if t == "datetime":
        ms = _iso_to_ms(v)
        return ms, {}, ms_to_iso(ms)
    if t == "date":
        day = _iso_date(v)
        j = r.journal(day, create=create_journals)
        if j is None:
            raise InvalidValue(f"no journal page for {day}")
        return j["id"], {"entityId": True}, day
    if t in REF_TYPES:
        e = _resolve_node(v, r, name)
        if prop.classes:
            allowed = r.allowed_tag_ids(prop.classes)
            if not (r.tag_ids(e) & allowed):
                raise InvalidValue(f"'{e.get('title')}' has none of the tags allowed for '{name}'")
        return e["id"], {"entityId": True}, {"uuid": e["uuid"], "title": e.get("title")}
    if t in ("default", "url", "string"):
        if not isinstance(v, str):
            raise InvalidValue(f"'{name}' is text, got {v!r}")
        if t == "url" and ":" not in v:
            raise InvalidValue(f"'{name}' is a URL, got {v!r}")
        return v, {}, v
    return v, {}, v  # rare built-in types (keyword, map, any): passed through


def _resolve_node(v: Any, r: Resolver, name: str) -> dict:
    if isinstance(v, dict):
        v = v.get("uuid") or v.get("title")
    if not isinstance(v, str) or not v:
        raise InvalidValue(f"'{name}' references a node: give its UUID or title, got {v!r}")
    if UUID_RE.match(v):
        e = r.entity_by_uuid(v)
        if e is None:
            raise InvalidValue(f"no node with UUID {v}")
        return e
    hits = r.entities_by_title(v)
    if len(hits) == 1:
        return hits[0]
    if not hits:
        raise InvalidValue(f"no page or tag titled '{v}' (for '{name}'); create it first or give a UUID")
    raise InvalidValue(f"title '{v}' is ambiguous for '{name}'; give a UUID: {', '.join(h['uuid'] for h in hits)}")
