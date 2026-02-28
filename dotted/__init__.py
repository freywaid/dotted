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
mutable(obj, key)       Check if update would mutate in place

Pattern Matching
----------------
match(pattern, key)     Match pattern to key
replace(template, bind) Substitute $N in template path
expand(obj, pattern)    Expand pattern to concrete keys

Building & Plucking
-------------------
build(obj, key)         Build default structure for key
pack(keyvalues)         Build object from key-value pairs
pluck(obj, pattern)     Extract field-value pairs
walk(obj, pattern)      Yield (path, value) pairs (lazy)
unpack(obj)             Extract to dotted normal form
keys(obj)               Dotted paths of obj
values(obj)             Leaf values of obj
items(obj)              (path, value) pairs as dict_items view

Transforms
----------
Append transforms with |: 'field|int', 'field|str:fmt'

    >>> dotted.get({'n': '42'}, 'n|int')
    42

register(name, fn)      Register custom transform
transform(name)         Decorator for custom transforms

Constants
---------
ANY                     Match any value (for remove)
AUTO                    Auto-infer root container type ({} or []) from first key

For full documentation including all options and flags:
    $ pydoc dotted.api
    or see README.md
"""
from .api import \
    parse, is_pattern, is_inverted, mutable, quote, normalize, ANY, AUTO, Attrs, GroupMode, \
    register, transform, \
    assemble, assemble_multi, \
    build, build_multi, \
    expand, expand_multi, \
    match, match_multi, replace, overlaps, translate, translate_multi, \
    apply, apply_multi, \
    get, get_multi, \
    has, setdefault, setdefault_multi, \
    update, update_multi, pack, update_if, update_if_multi, \
    remove, remove_multi, remove_if, remove_if_multi, \
    pluck, pluck_multi, walk, walk_multi, unpack, keys, values, items

__all__ = [
    # Core
    'get', 'update', 'remove', 'has', 'setdefault',
    # Multi
    'get_multi', 'update_multi', 'pack', 'update_if', 'update_if_multi', 'remove_multi', 'remove_if', 'remove_if_multi', 'setdefault_multi',
    # Pattern
    'match', 'match_multi', 'replace', 'overlaps', 'translate', 'translate_multi', 'expand', 'expand_multi',
    # Build/Pluck
    'build', 'build_multi', 'pluck', 'pluck_multi', 'walk', 'walk_multi', 'unpack', 'keys', 'values', 'items',
    # Transform
    'apply', 'apply_multi', 'register', 'transform',
    # Utility
    'parse', 'assemble', 'assemble_multi', 'quote', 'normalize', 'is_pattern', 'is_inverted', 'mutable',
    # Constants
    'ANY', 'AUTO', 'Attrs', 'GroupMode',
]
