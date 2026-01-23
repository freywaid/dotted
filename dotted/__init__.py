"""
Dotted notation for safe nested data traversal with optional chaining,
pattern matching, and transforms.

Missing keys and attributes are handled gracefully—no KeyError or AttributeError.
Use dotted to fetch, update, and remove data from deeply nested structures.

    >>> import dotted
    >>> d = {'hi': {'there': [1, 2, 3]}}
    >>> dotted.get(d, 'hi.there[1]')
    2

Core Operations
---------------
get(obj, key)           Get value at dotted key
update(obj, key, val)   Update value at dotted key
remove(obj, key)        Remove value at dotted key
has(obj, key)           Check if key exists
setdefault(obj, k, v)   Set value only if key missing

Pattern Matching
----------------
match(pattern, key)     Match pattern to key
expand(obj, pattern)    Expand pattern to concrete keys

Building & Plucking
-------------------
build(obj, key)         Build default structure for key
pluck(obj, pattern)     Extract field-value pairs

Transforms
----------
Append transforms with |: 'field|int', 'field|str:fmt'

    >>> dotted.get({'n': '42'}, 'n|int')
    42

register(name, fn)      Register custom transform
transform(name)         Decorator for custom transforms
"""
from .api import \
    parse, is_pattern, is_inverted, quote, ANY, \
    register, transform, \
    assemble, assemble_multi, \
    build, build_multi, \
    expand, expand_multi, \
    match, match_multi, \
    apply, apply_multi, \
    get, get_multi, \
    has, setdefault, setdefault_multi, \
    update, update_multi, \
    remove, remove_multi, \
    pluck, pluck_multi

__all__ = [
    # Core
    'get', 'update', 'remove', 'has', 'setdefault',
    # Multi
    'get_multi', 'update_multi', 'remove_multi', 'setdefault_multi',
    # Pattern
    'match', 'match_multi', 'expand', 'expand_multi',
    # Build/Pluck
    'build', 'build_multi', 'pluck', 'pluck_multi',
    # Transform
    'apply', 'apply_multi', 'register', 'transform',
    # Utility
    'parse', 'assemble', 'assemble_multi', 'quote', 'is_pattern', 'is_inverted',
    # Constants
    'ANY',
]
