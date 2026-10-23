"""Runtime configuration, read from the environment and validated once.

Loading is lazy: importing this module has no side effects, so the package
imports without ``LOGSEQ_API_TOKEN``. ``get_settings()`` validates at startup.
"""

import functools
import logging
import math
import os
from dataclasses import dataclass
from urllib.parse import urlparse

logger = logging.getLogger("logseq-db-mcp")


@dataclass(frozen=True)
class Settings:
    api_key: str
    # scheme://host:port, without path. The API lives at /api, Logseq's own
    # MCP server (needed for upsertNodes) at /mcp.
    base_url: str
    verify_ssl: bool
    connect_timeout: float
    read_timeout: float
    hide_tags: tuple[str, ...] = ()
    write_tags: tuple[str, ...] = ()
    write_pages: tuple[str, ...] = ()
    enable_call_api: bool = False

    @property
    def timeout(self) -> tuple[float, float]:
        return (self.connect_timeout, self.read_timeout)

    @property
    def has_access_rules(self) -> bool:
        return bool(self.hide_tags or self.write_tags or self.write_pages)


def _float_env(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        value = math.nan
    if not math.isfinite(value) or value <= 0:
        logger.warning(f"{name} must be a positive number of seconds, got {raw!r}; using {default}")
        return default
    return value


def _csv_env(name: str) -> tuple[str, ...]:
    return tuple(v.strip() for v in os.getenv(name, "").split(",") if v.strip())


def _flag_env(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in ("1", "true", "yes")


def load_settings() -> Settings:
    """Read and validate the configuration. Raises ``ValueError`` without a token."""
    api_key = os.getenv("LOGSEQ_API_TOKEN", "")
    if not api_key:
        raise ValueError("LOGSEQ_API_TOKEN environment variable required")

    url = urlparse(os.getenv("LOGSEQ_API_URL", "http://127.0.0.1:12315"))
    scheme = url.scheme or "http"
    # Logseq's /mcp endpoint rejects "Host: localhost"; 127.0.0.1 works for both.
    host = url.hostname or "127.0.0.1"
    if host == "localhost":
        host = "127.0.0.1"
    verify_env = os.getenv("LOGSEQ_VERIFY_SSL")
    verify_ssl = verify_env.lower() not in ("0", "false", "no") if verify_env else scheme == "https"

    return Settings(
        api_key=api_key,
        base_url=f"{scheme}://{host}:{url.port or 12315}",
        verify_ssl=verify_ssl,
        connect_timeout=_float_env("LOGSEQ_API_CONNECT_TIMEOUT", 3),
        read_timeout=_float_env("LOGSEQ_API_READ_TIMEOUT", 15),
        hide_tags=_csv_env("LOGSEQ_HIDE_TAGS"),
        write_tags=_csv_env("LOGSEQ_WRITE_TAGS"),
        write_pages=_csv_env("LOGSEQ_WRITE_PAGES"),
        enable_call_api=_flag_env("LOGSEQ_ENABLE_CALL_API"),
    )


@functools.cache
def get_settings() -> Settings:
    """The process-wide settings; ``get_settings.cache_clear()`` reloads (tests)."""
    return load_settings()
