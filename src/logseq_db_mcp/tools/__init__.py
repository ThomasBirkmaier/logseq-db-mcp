"""All tools, in the order the client lists them."""

from .base import Context, Tool


def all_tools() -> list[Tool]:
    from .raw import CallApi
    from .read import READ_TOOLS
    from .validate import ValidateObjects
    from .write import WRITE_TOOLS

    return [*READ_TOOLS, ValidateObjects(), *WRITE_TOOLS, CallApi()]


__all__ = ["Context", "Tool", "all_tools"]
