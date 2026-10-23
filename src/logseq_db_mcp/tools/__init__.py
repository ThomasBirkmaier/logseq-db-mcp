"""All tools, in the order the client lists them."""

from .base import Context, Tool


def all_tools() -> list[Tool]:
    from .raw import CallApi
    from .read import READ_TOOLS
    from .write import WRITE_TOOLS

    return [*READ_TOOLS, *WRITE_TOOLS, CallApi()]


__all__ = ["Context", "Tool", "all_tools"]
