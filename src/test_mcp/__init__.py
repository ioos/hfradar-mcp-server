"""Independent HF radar MCP servers; the default exports refer to hfradmin."""

__all__ = ["main", "mcp"]


def __getattr__(name):
    # Loading an optional server must not initialize the default API client.
    if name in __all__:
        from importlib import import_module

        return getattr(import_module(".server", __name__), name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
