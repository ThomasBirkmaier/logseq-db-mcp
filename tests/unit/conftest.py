import json
from pathlib import Path

import pytest

from logseq_db_mcp.client import LogseqClient, LogseqError
from logseq_db_mcp.settings import Settings

FIXTURES = Path(__file__).parent.parent / "fixtures"


def settings(**overrides) -> Settings:
    base = dict(api_key="test", base_url="http://127.0.0.1:12315", verify_ssl=False,
                connect_timeout=1, read_timeout=1)
    return Settings(**{**base, **overrides})


class ReplayClient(LogseqClient):
    """Answers calls from a recording (scripts/record_fixtures.py); unknown calls fail."""

    def __init__(self, calls: list[dict], s: Settings | None = None):
        super().__init__(s or settings())
        self.answers: dict[tuple, list] = {}
        for c in calls:
            self.answers.setdefault((c["kind"], c["name"], json.dumps(c["args"])), []).append(c["result"])
        self.seen: list[tuple] = []

    def _answer(self, kind: str, name: str, args: list):
        key = (kind, name, json.dumps(args))
        self.seen.append(key)
        if key not in self.answers:
            raise AssertionError(f"unrecorded call {name} {args}")
        queue = self.answers[key]
        return queue.pop(0) if len(queue) > 1 else queue[0]

    def call(self, method: str, *args):
        body = self._answer("api", method, list(args))
        if isinstance(body, dict) and set(body) == {"error"}:
            raise LogseqError(f"{method}: {body['error']}")
        return body

    def mcp_tool(self, tool: str, arguments: dict) -> str:
        return self._answer("mcp", tool, [arguments])


class FakeClient(LogseqClient):
    """Records calls and answers from a function; for tests that need no real data."""

    def __init__(self, respond=None, s: Settings | None = None):
        super().__init__(s or settings())
        self.respond = respond or (lambda method, args: None)
        self.calls: list[tuple] = []

    def call(self, method: str, *args):
        self.calls.append((method, args))
        return self.respond(method, args)


@pytest.fixture(scope="session")
def recording():
    return json.loads((FIXTURES / "spiketest.json").read_text())
