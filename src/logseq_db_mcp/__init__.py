"""logseq-db-mcp: a DB-native MCP server for Logseq DB graphs."""

import argparse
import asyncio
import logging
import os
import sys

_LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"


def parse_args(argv=None):
    p = argparse.ArgumentParser(prog="logseq-db-mcp")
    p.add_argument("--read-only", action="store_true", help="Offer only the reading tools.")
    p.add_argument("--log-file", default=os.getenv("LOGSEQ_LOG_FILE"),
                   help="Also write the log to this file (stderr always).")
    p.add_argument("--log-level", default=os.getenv("LOGSEQ_LOG_LEVEL", "INFO"))
    return p.parse_args(argv)


def _setup_logging(level: str, log_file: str | None) -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]
    if log_file:
        try:
            handlers.append(logging.FileHandler(log_file))
        except OSError as e:
            print(f"logseq-db-mcp: cannot open log file {log_file}: {e}", file=sys.stderr)
    logging.basicConfig(level=getattr(logging, level.upper(), logging.INFO), format=_LOG_FORMAT,
                        handlers=handlers)
    # the MCP SDK logs full requests at DEBUG; keep content out of the log
    logging.getLogger("mcp").setLevel(max(logging.INFO, logging.getLogger().level))


def main(argv=None):
    args = parse_args(argv)
    _setup_logging(args.log_level, args.log_file)
    from .server import serve_stdio
    from .settings import get_settings

    try:
        settings = get_settings()
    except ValueError as e:
        raise SystemExit(f"logseq-db-mcp: {e}")
    asyncio.run(serve_stdio(settings, read_only=args.read_only))
