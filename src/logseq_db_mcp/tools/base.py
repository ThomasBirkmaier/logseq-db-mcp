"""Tool base class and the shared context every tool runs with."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from mcp.types import Tool as McpTool, ToolAnnotations

from ..access import Access
from ..client import LogseqClient
from ..graph import Graph
from ..schema import SchemaCache
from ..settings import Settings


@dataclass
class Context:
    settings: Settings
    client: LogseqClient
    schema: SchemaCache
    graph: Graph
    access: Access

    @classmethod
    def create(cls, settings: Settings, client: LogseqClient | None = None) -> "Context":
        client = client or LogseqClient(settings)
        schema = SchemaCache(client)
        graph = Graph(client, schema, settings)
        return cls(settings, client, schema, graph, Access(graph, settings))


UUID_SCHEMA = {"type": "string", "pattern": "^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"}


class Tool:
    name: str = ""
    description: str = ""
    input_schema: dict = {"type": "object", "properties": {}}
    #: mutates the graph: dropped under --read-only, audited
    writes: bool = False
    destructive: bool = False

    def describe(self) -> McpTool:
        return McpTool(
            name=self.name,
            description=self.description,
            input_schema=self.input_schema,
            annotations=ToolAnnotations(read_only_hint=not self.writes, destructive_hint=self.destructive,
                                        open_world_hint=False),
        )

    def run(self, ctx: Context, args: dict) -> Any:
        raise NotImplementedError
