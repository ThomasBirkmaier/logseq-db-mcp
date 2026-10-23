"""Access rules, DB-native: by tag and page instead of namespaces.

- ``LOGSEQ_HIDE_TAGS``: nodes with one of these tags, or on a page with one,
  are never read or written.
- ``LOGSEQ_WRITE_PAGES`` / ``LOGSEQ_WRITE_TAGS``: when either is set, writes
  are allowed only on those pages, or on nodes (or pages) carrying those tags.
- ``call_api`` cannot be checked against any rule and is denied while one is set.
"""

from __future__ import annotations

from .graph import Graph
from .settings import Settings


class AccessDenied(RuntimeError):
    pass


class Access:
    def __init__(self, graph: Graph, settings: Settings):
        self.graph = graph
        self.settings = settings

    @property
    def write_limited(self) -> bool:
        return bool(self.settings.write_pages or self.settings.write_tags)

    def check_read(self, e: dict, page: dict | None = None) -> None:
        if self.graph.hidden(e, page):
            raise AccessDenied(f"Access denied: node {e.get('uuid')} is hidden from this assistant.")

    def check_write(self, e: dict, page: dict | None = None) -> None:
        page = page if page is not None else self.graph.page_of(e)
        self.check_read(e, page)
        if not self.write_limited:
            return
        if (page.get("title") or "").lower() in {p.lower() for p in self.settings.write_pages}:
            return
        write_tags = self.graph.tag_ids_for_names(self.settings.write_tags)
        if write_tags & (self.graph.tag_ids(e) | self.graph.tag_ids(page)):
            return
        raise AccessDenied(f"Access denied: '{page.get('title')}' is read-only for this assistant.")

    def check_tags_visible(self, tag_ids: set[int]) -> None:
        if self.settings.hide_tags and tag_ids & self.graph.tag_ids_for_names(self.settings.hide_tags):
            raise AccessDenied("Access denied: these tags are hidden from this assistant.")

    def check_new(self, title: str, tag_ids: set[int]) -> None:
        """For a node that does not exist yet (a new page or object)."""
        self.check_tags_visible(tag_ids)
        if not self.write_limited:
            return
        if title.lower() in {p.lower() for p in self.settings.write_pages}:
            return
        if tag_ids & self.graph.tag_ids_for_names(self.settings.write_tags):
            return
        raise AccessDenied(f"Access denied: creating '{title}' is outside the write rules.")

    def check_raw(self) -> None:
        if self.settings.has_access_rules:
            raise AccessDenied(
                "Access denied: call_api cannot check access rules and is disabled while "
                "LOGSEQ_HIDE_TAGS, LOGSEQ_WRITE_TAGS or LOGSEQ_WRITE_PAGES is set.")
