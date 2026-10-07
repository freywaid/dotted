"""
Shared type-checking helpers (duck-typing).
"""
import re

try:
    # C implementation of copy.deepcopy with identical semantics;
    # installed by the [copium] extra.
    from copium import deepcopy
except ImportError:
    from copy import deepcopy

def indices(iterable):
    """
    The index of each item of iterable, lazily.
    """
    for idx, _ in enumerate(iterable):
        yield idx


class lazyprop:
    """
    Non-data descriptor: compute once on first access, cache the result
    in the instance __dict__ (which then shadows the descriptor). Like
    functools.cached_property but lock-free and available on 3.6+.
    """
    def __init__(self, fn):
        self.fn = fn
        self.name = fn.__name__
        self.__doc__ = fn.__doc__

    def __get__(self, obj, owner=None):
        if obj is None:
            return self
        val = self.fn(obj)
        obj.__dict__[self.name] = val
        return val


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


# ---- quoting utilities ----

_RESERVED = frozenset('.[]*:|+?/=,@&()!~#{}$<>')
_NEEDS_QUOTE = _RESERVED | frozenset(' \t\n\r')

_NUMERIC_RE = re.compile(
    r'[-]?0[xX][0-9a-fA-F]+$'           # hex
    r'|[-]?0[oO][0-7]+$'                # octal
    r'|[-]?0[bB][01]+$'                 # binary
    r'|[-]?[0-9][0-9_]*[eE][+-]?[0-9]+$' # scientific notation
    r'|[-]?[0-9]+(?:_[0-9]+)+$'         # underscore separators
    r'|[-]?[0-9]+$'                     # plain integers
)


def needs_quoting(s):
    """
    Return True if a string key must be quoted in dotted notation.
    """
    if not s:
        return True
    # Numeric forms (integers, scientific notation, underscore separators)
    # are handled by the grammar and don't need quoting, even if they
    # contain reserved characters like '+' in '1e+10'.
    if s[0].isdigit() or (len(s) > 1 and s[0] == '-' and s[1].isdigit()):
        return not _NUMERIC_RE.match(s)
    return not _NEEDS_QUOTE.isdisjoint(s)


def is_numeric_str(s):
    """
    Return True if s is a string that parses as an integer.
    """
    # int() cannot accept a string that starts with a letter; answering
    # here avoids raising and catching for every ordinary key
    if type(s) is str and s[:1].isalpha():
        return False
    try:
        int(s)
        return True
    except (ValueError, TypeError):
        return False


def quote_str(s):
    """
    Wrap a string in single quotes, escaping backslashes and single quotes.
    """
    s = s.replace('\\', '\\\\').replace("'", "\\'")
    return f"'{s}'"


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
