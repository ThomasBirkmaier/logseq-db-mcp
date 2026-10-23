"""Links inside block titles.

Logseq stores a link as ``[[<uuid>]]``. Outgoing text shows ``[[Title]]``
instead; incoming text keeps ``[[Title]]``, which Logseq links to the existing
page of that title. A link to a title that does not exist would make Logseq
create an empty page, so writers check links first (``missing_links``).
"""

import re

UUID_REF_RE = re.compile(r"\[\[([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\]\]")
LINK_RE = re.compile(r"\[\[([^\[\]]+)\]\]")


def uuids_in(text: str | None) -> set[str]:
    return set(UUID_REF_RE.findall(text or ""))


def render(text: str | None, titles: dict[str, str]) -> str:
    """``[[uuid]]`` → ``[[Title]]`` where the title is known."""
    def sub(m: re.Match) -> str:
        title = titles.get(m.group(1))
        return f"[[{title}]]" if title else m.group(0)
    return UUID_REF_RE.sub(sub, text or "")


def title_links(text: str | None) -> list[str]:
    """Linked titles in ``text`` (``[[uuid]]`` links excluded), in order, unique."""
    seen: list[str] = []
    for m in LINK_RE.finditer(text or ""):
        target = m.group(1).strip()
        if not UUID_REF_RE.fullmatch(f"[[{target}]]") and target not in seen:
            seen.append(target)
    return seen
