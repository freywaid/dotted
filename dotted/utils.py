"""
Shared type-checking helpers (duck-typing).
"""

try:
    import dataclasses as _dc
except ImportError:
    # Python 3.6 ships without dataclasses; treat everything as non-dataclass.
    _dc = None


def is_dataclass(obj):
    """
    True if `obj` is a dataclass (class or instance). Returns False on
    interpreters without the stdlib `dataclasses` module (Python 3.6).
    """
    return _dc is not None and _dc.is_dataclass(obj)


def dataclass_replace(obj, **changes):
    """
    Thin wrapper over `dataclasses.replace(obj, **changes)`. Call only
    after `is_dataclass(obj)` is True — raises on interpreters without
    the module.
    """
    if _dc is None:
        raise RuntimeError('dataclasses module is unavailable')
    return _dc.replace(obj, **changes)


def is_dict_like(node):
    """
    True if node is dict-like: has .keys() and __getitem__.
    """
    return (
        hasattr(node, 'keys') and callable(node.keys)
        and hasattr(node, '__getitem__')
    )


def is_list_like(node):
    """
    True if node is list-like: has __getitem__, not str/bytes, not dict-like.
    """
    return (
        hasattr(node, '__getitem__')
        and not isinstance(node, (str, bytes))
        and not is_dict_like(node)
    )


def is_set_like(node):
    """
    True if node is set-like: iterable, no __getitem__.
    """
    return (
        hasattr(node, '__iter__')
        and not hasattr(node, '__getitem__')
    )


def is_terminal(node):
    """
    True if node is a terminal value (not dict-like, list-like, or set-like).
    """
    return not is_dict_like(node) and not is_list_like(node) and not is_set_like(node)
