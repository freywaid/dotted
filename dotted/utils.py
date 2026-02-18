"""
Shared type-checking helpers (duck-typing).
"""


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
