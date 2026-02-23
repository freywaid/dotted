"""
Main api
"""
import copy
import enum
import functools
import itertools
import pyparsing as pp
from . import grammar
from . import engine
from . import base
from . import access
from . import utypes


class Attrs(enum.StrEnum):
    """
    Feature flags for unpack attr inclusion.
    """
    standard = 'standard'
    special = 'special'

CACHE_SIZE = 300
ANY = utypes.ANY
AUTO = type('AUTO', (), {'__repr__': lambda self: 'AUTO'})()


def _auto_root_from_key(key):
    """
    Infer root container type from a single key.
    Returns [] if root is a sequence, {} otherwise.
    """
    ops = parse(key)
    if ops.ops and isinstance(ops.ops[0], access.Slot):
        return []
    return {}


def _auto_root(keyvalues):
    """
    Infer root container type from the first key in keyvalues.
    Returns [] if root is a sequence, {} otherwise.
    """
    for item in keyvalues:
        key = item[0] if isinstance(item, (list, tuple)) else item
        return _auto_root_from_key(key)
    return {}


class ParseError(Exception):
    """Raised when dotted notation cannot be parsed."""
    pass


@functools.lru_cache(CACHE_SIZE)
def _parse(ops):
    try:
        results = grammar.template.parse_string(ops, parse_all=True)
    except pp.ParseException as e:
        raise ParseError(f"Invalid dotted notation: {e.msg}\n  {repr(ops)}\n  {' ' * e.loc}^") from None
    return engine.Dotted(results)


def parse(key):
    """
    Parse dotted notation. Results are LRU-cached (same path string reuses cached parse).
    >>> parse('hello.there|str:"=%s"')
    Dotted([hello, there], [('str', '=%s')])
    """
    if isinstance(key, engine.Dotted):
        return key
    if isinstance(key, tuple):
        return engine.Dotted({'ops': key, 'transforms': ()})
    return _parse(key)


def quote(key, as_key=True):
    """
    Quote a key for use in a dotted path.  Wraps in single quotes if
    the key contains reserved characters or whitespace.
    >>> quote('hello')
    'hello'
    >>> quote(7)
    '7'
    >>> quote(7, as_key=False)
    '7'
    >>> quote(7.2)
    "#'7.2'"
    >>> quote(7.2, as_key=False)
    '7.2'
    >>> quote('7')
    '7'
    >>> quote('a.b')
    "'a.b'"
    """
    return access.quote(key, as_key=as_key)


def normalize(key, as_key=True):
    """
    Convert a raw Python key to dotted normal form.  Like quote(), but
    also quotes string keys that look numeric so they round-trip correctly
    through pack/unpack (preserving string vs int key type).
    >>> normalize('hello')
    'hello'
    >>> normalize(7)
    '7'
    >>> normalize('7')
    "'7'"
    >>> normalize('a.b')
    "'a.b'"
    """
    return access.normalize(key, as_key=as_key)


@functools.lru_cache(CACHE_SIZE)
def _is_pattern(ops):
    if not ops:
        return False
    if ops[0].is_pattern():
        return True
    return _is_pattern(ops[1:])


def is_pattern(key):
    """
    True if dotted is a pattern
    >>> is_pattern('hello.*')
    True
    >>> is_pattern('hello.there')
    False
    """
    if isinstance(key, engine.Dotted):
        return _is_pattern(key)
    return _is_pattern(parse(key))


def is_inverted(key):
    """
    True if an inverted style pattern
    >>> is_inverted('-hello.there')
    True
    >>> is_inverted('hello.there')
    False
    """
    ops = parse(key)
    return isinstance(ops[0], access.Invert)


def _is_mutable_container(obj):
    """
    Check if obj is a mutable container type.
    """
    # Common immutable types
    if isinstance(obj, (str, bytes, tuple, frozenset)):
        return False
    # Namedtuples
    if hasattr(obj, '_fields') and hasattr(obj, '_replace'):
        return False
    # Frozen dataclasses
    import dataclasses
    if dataclasses.is_dataclass(obj) and obj.__dataclass_fields__:
        # Check if frozen
        try:
            # Try to detect frozen - frozen dataclasses raise FrozenInstanceError
            if hasattr(obj, '__dataclass_params__') and obj.__dataclass_params__.frozen:
                return False
        except (AttributeError, TypeError):
            pass
    # Check for common mutable types
    if isinstance(obj, (dict, list, set)):
        return True
    # Check if it has mutable dict
    if hasattr(obj, '__dict__'):
        return True
    # Has __setitem__ suggests mutability
    if hasattr(obj, '__setitem__'):
        return True
    return False


def mutable(obj, key, strict=False):
    """
    Check if update(obj, key, val) would mutate obj in place.

    Returns False if:
    - The path is empty (root replacement, not mutation)
    - All containers along the path are immutable

    Returns True if any mutable container along the path would be mutated.

    >>> mutable({'a': 1}, 'a')
    True
    >>> mutable({'a': 1}, '')
    False
    >>> mutable((1, 2), '[0]')
    False
    >>> mutable([1, 2], '[0]')
    True
    >>> mutable({'a': (1, 2)}, 'a[0]')
    True
    >>> mutable({'a': {'b': 1}}, 'a.b')
    True
    >>> mutable(({'a': 1},), '[0].a')
    True
    >>> mutable(((1, 2),), '[0][0]')
    False
    """
    ops = parse(key)

    # Empty path can never mutate (can't rebind caller's variable)
    if len(ops) == 1 and isinstance(ops[0], access.Empty):
        return False

    # Walk the path - if we find any mutable container, mutation will occur
    current = obj
    for op in ops:
        if isinstance(op, access.Invert):
            continue

        if _is_mutable_container(current):
            return True

        # Traverse to next level (consume only first value)
        _marker = object()
        first = next(op.values(current, strict=strict), _marker)
        if first is _marker:
            # Path doesn't exist - would be created, but parent is immutable
            return False
        current = first

    return False


# Alias for use inside functions where 'mutable' parameter shadows the function
_mutable = mutable


def build_multi(obj, keys, strict=False):
    """
    Build a subset/default obj based on concrete key fields
    >>> build_multi({}, ('hello.bye[]', 'hello.there', ))
    {'hello': {'bye': [], 'there': None}}
    """
    keys = tuple(keys)
    if obj is AUTO:
        obj = _auto_root(((k, None) for k in keys))
    for key in keys:
        built = engine.build(parse(key), obj, strict=strict)
        obj = update_multi(obj, pluck_multi(built, (key,), strict=strict), strict=strict)
    return obj


def build(obj, key, strict=False):
    """
    Build a subset/default obj based on dotted
    >>> build({}, 'hello.there')
    {'hello': {'there': None}}
    >>> # build({}, 'a.b.c[:2].d')
    """
    return build_multi(obj, (key,), strict=strict)


def get(obj, key, default=None, pattern_default=(), apply_transforms=True, strict=False):
    """
    Get a value specified by the dotted key. If dotted is a pattern,
    return a tuple of all matches.

    Cut (#) in disjunction: if a branch has # and it matches, only its results
    are returned and later branches are not tried.
    >>> d = {'hello': {'there': [1, '2', 3]}}
    >>> get(d, 'hello.there[1]|int')
    2
    >>> get(d, 'hello.there[1:]')
    ['2', 3]
    >>> get([{'a': 1}, {'a':2}], '[*].a')
    (1, 2)
    >>> get({'a': 1, 'b': 2}, '(a#, b)')   # cut: first branch matches, so (1,) only
    (1,)
    >>> get({'b': 2}, '(a#, b)')           # a missing, so try b
    (2,)

    Recursive traversal with ** (all depths) and *key (chain-following):
    >>> get({'a': {'b': {'c': 1}}, 'x': {'b': {'c': 2}}}, '**.c')
    (1, 2)
    >>> get({'b': {'b': {'c': 1}}}, '*b.c')
    (1,)
    >>> get({'a': {'b': 7, 'c': 3}}, '**=7')
    (7,)
    """
    ops = parse(key)
    vals = engine.iter_until_cut(engine.gets(ops, obj, strict=strict))
    if apply_transforms:
        vals = ( ops.apply(v) for v in vals )
    found = tuple(vals)
    if not is_pattern(ops):
        return found[0] if found else default
    return found if found else pattern_default


def get_multi(obj, iterable, apply_transforms=True, strict=False):
    """
    Get all values from obj in iterable; return iterable
    >>> list(get_multi({'hello': 7, 'there': 9}, ['hello', 'there']))
    [7, 9]
    """
    dummy = object()
    found = (get(obj, k, dummy, dummy, apply_transforms=apply_transforms, strict=strict) for k in iterable)
    return (v for v in found if v is not dummy)


def has(obj, key, strict=False):
    """
    True if key/pattern is contained in obj
    >>> d = {'hello': {'there': [1, '2', 3]}}
    >>> has(d, 'hello.*')
    True
    >>> has(d, 'hello.bye')
    False
    """
    dummy = object()
    return get(obj, key, dummy, dummy, strict=strict) is not dummy


def setdefault(obj, key, val, apply_transforms=True, strict=False):
    """
    Get value at path if it exists, else set path to val and return that value (like dict.setdefault).
    >>> d = {'hello': 'there'}
    >>> setdefault(d, 'hello', 'world')
    'there'
    >>> setdefault(d, 'bye', 'world')
    'world'
    >>> setdefault({}, 'a.b.c', 7)
    7
    """
    if obj is AUTO:
        obj = _auto_root_from_key(key)
    if has(obj, key, strict=strict):
        return  get(obj, key, apply_transforms=apply_transforms, strict=strict)
    obj = update(obj, key, val, apply_transforms=apply_transforms, strict=strict)
    return get(obj, key, apply_transforms=False, strict=strict)


def setdefault_multi(obj, keyvalues, apply_transforms=True, strict=False):
    """
    For each (key, value), set value at key only if key does not exist (like setdefault).
    Returns an iterable of the value at each path (same order as keyvalues), like get_multi.
    Mutates obj in place.
    >>> d = {'a': 1}
    >>> list(setdefault_multi(d, [('a', 999), ('b', 2)]))
    [1, 2]
    >>> d
    {'a': 1, 'b': 2}
    """
    for k, v in keyvalues.items() if hasattr(keyvalues, 'items') else keyvalues:
        yield setdefault(obj, k, v, apply_transforms=apply_transforms, strict=strict)


def update_if(obj, key, val, pred=lambda val: val is not None, mutable=True, apply_transforms=True, strict=False):
    """
    Update only when pred(val) is true.  Default pred skips None values.
    Use pred=None for unconditional update (same as update).

    >>> update_if({}, 'a', 1)
    {'a': 1}
    >>> update_if({}, 'a', None)
    {}
    >>> update_if({}, 'a', 0)
    {'a': 0}
    >>> update_if({}, 'a', '', pred=bool)
    {}
    """
    if obj is AUTO:
        obj = _auto_root_from_key(key)
    if not mutable and _is_mutable_container(obj):
        obj = copy.deepcopy(obj)
        mutable = True

    if pred is not None and not pred(val):
        return obj

    ops = parse(key)
    return engine.updates(ops, obj, ops.apply(val) if apply_transforms else val, strict=strict)


def update_if_multi(obj, items, pred=lambda val: val is not None, mutable=True, apply_transforms=True, strict=False):
    """
    Update multiple keys, skipping items where pred(val) is false.
    items: iterable of (key, val) or (key, val, pred).
    >>> update_if_multi({}, [('a', 1), ('b', None), ('c', 3)])
    {'a': 1, 'c': 3}
    """
    if not mutable and _is_mutable_container(obj):
        obj = copy.deepcopy(obj)
        mutable = True
    for item in items:
        key, val, *rest = item
        p = rest[0] if rest else pred
        obj = update_if(obj, key, val, pred=p, mutable=mutable, apply_transforms=apply_transforms, strict=strict)
    return obj


def update(obj, key, val, mutable=True, apply_transforms=True, strict=False):
    """
    Update obj with all matches to dotted key with val
    >>> d = {'hello': {'there': {'stuff': 1}}}
    >>> update(d, 'hello.there.stuff', 2)
    {'hello': {'there': {'stuff': 2}}}
    >>> update({}, 'a.b.c[]', [2, 3])
    {'a': {'b': {'c': [2, 3]}}}
    >>> d = {}
    >>> update(d, 'queries[+].name', 'hello')
    {'queries': [{'name': 'hello'}]}
    >>> update(d, 'queries[+]', 'bye')
    {'queries': [{'name': 'hello'}, 'bye']}
    >>> update([1, 2], '[*?]', 'hello')
    ['hello', 2]
    >>> update({}, 'hello.7.me', 'bye')             # coerces to numeric
    {'hello': {7: {'me': 'bye'}}}
    >>> update({}, 'hello.#"7.0".me', 'bye')        # coerces to numeric
    {'hello': {7.0: {'me': 'bye'}}}
    >>> update({'hello': {'there': {'me': 'bye'}}}, '-hello.there', ANY)    # invert
    {'hello': {}}

    NOP (~) with first-match: update only if key missing
    >>> update({'name': {'first': 'alice'}}, '(name.~first, name.first)?', 'bob')
    {'name': {'first': 'alice'}}
    >>> update({'name': {}}, '(name.~first, name.first)?', 'bob')
    {'name': {'first': 'bob'}}

    Recursive update with value guard:
    >>> update({'a': {'b': 7, 'c': 3}, 'd': 7}, '**=7', 99)
    {'a': {'b': 99, 'c': 3}, 'd': 99}

    Use mutable=False to prevent mutation of the original object:
    >>> d = {'a': 1}
    >>> result = update(d, 'a', 2, mutable=False)
    >>> d
    {'a': 1}
    >>> result
    {'a': 2}
    """
    return update_if(obj, key, val, pred=None, mutable=mutable, apply_transforms=apply_transforms, strict=strict)


def update_multi(obj, keyvalues, mutable=True, apply_transforms=True, strict=False):
    """
    Update obj with all keyvalues.  Pass AUTO as obj to infer the root
    container type from the first key.
    >>> update_multi({}, [('hello.there', 7), ('my.my', 9)])
    {'hello': {'there': 7}, 'my': {'my': 9}}
    >>> update_multi({}, {'stuff.more.stuff': 'mine'})
    {'stuff': {'more': {'stuff': 'mine'}}}
    >>> update_multi(AUTO, [('[0]', 'a'), ('[1]', 'b')])
    ['a', 'b']
    """
    if hasattr(keyvalues, 'items') and callable(keyvalues.items):
        keyvalues = keyvalues.items()
    if obj is AUTO:
        keyvalues = list(keyvalues)
        obj = _auto_root(keyvalues)
    return update_if_multi(obj, keyvalues, pred=None, mutable=mutable, apply_transforms=apply_transforms, strict=strict)


def remove_if(obj, key, pred=lambda key: key is not None, val=ANY, mutable=True, strict=False):
    """
    Remove only when pred(key) is true.  Default pred skips None keys.
    Use pred=None for unconditional remove (same as remove).

    >>> remove_if({'a': 1}, 'a')
    {}
    >>> remove_if({'a': 1}, None)
    {'a': 1}
    """
    if obj is AUTO:
        obj = _auto_root_from_key(key)
    if not mutable and _is_mutable_container(obj):
        obj = copy.deepcopy(obj)
        mutable = True

    if pred is not None and not pred(key):
        return obj

    return engine.removes(parse(key), obj, val, strict=strict)


def remove_if_multi(obj, items, keys_only=True, pred=lambda key: key is not None, mutable=True, strict=False):
    """
    Remove by keys or (key, val, pred), skipping items where pred(key) is false.
    Default pred skips None keys.
    >>> remove_if_multi({'a': 1, 'b': 2}, ['a', None, 'b'])
    {}
    """
    if not mutable and _is_mutable_container(obj):
        obj = copy.deepcopy(obj)
        mutable = True

    if keys_only:
        for k in items:
            obj = remove_if(obj, k, pred=pred, val=ANY, mutable=mutable, strict=strict)
        return obj

    for item in items:
        k, v, *rest = item
        p = rest[0] if rest else pred
        obj = remove_if(obj, k, pred=p, val=v, mutable=mutable, strict=strict)
    return obj


def remove(obj, key, val=ANY, mutable=True, strict=False):
    """
    To remove all matches to `key`
        remove(obj, key) or remove(obj, key, ANY)
    To remove all matches to `key` with `val`
        remove(obj, key, val)
    >>> d = {'hello': {'there': [1, 2, 3]}}
    >>> remove(d, 'hello.there[-1]')
    {'hello': {'there': [1, 2]}}
    >>> remove(d, 'hello.there[*]', 1)
    {'hello': {'there': [2]}}
    >>> remove(d, 'hello.there', [2])
    {'hello': {}}
    >>> remove({}, '-hello.there', [2])
    {'hello': {'there': [2]}}

    Recursive remove with value guard:
    >>> remove({'a': {'b': 7, 'c': 3}, 'd': 7}, '**=7')
    {'a': {'c': 3}}

    Use mutable=False to prevent mutation of the original object:
    >>> d = {'a': 1, 'b': 2}
    >>> result = remove(d, 'a', mutable=False)
    >>> d
    {'a': 1, 'b': 2}
    >>> result
    {'b': 2}
    """
    return remove_if(obj, key, pred=None, val=val, mutable=mutable, strict=strict)


def remove_multi(obj, iterable, keys_only=True, mutable=True, strict=False):
    """
    Remove by keys or key-values
    >>> remove_multi({'hello': {'there': 7}, 'my': {'precious': 9}}, ['hello', 'my.precious'])
    {'my': {}}
    """
    if keys_only:
        return remove_if_multi(obj, iterable, keys_only=True, pred=None, mutable=mutable, strict=strict)
    if hasattr(iterable, 'items') and callable(iterable.items):
        iterable = iterable.items()
    return remove_if_multi(obj, iterable, keys_only=False, pred=None, mutable=mutable, strict=strict)


def _match_ops(pats, keys, partial):
    """
    Recursive match of pattern ops against key ops.
    Returns list of match values on success, None on failure.
    Handles Recursive ops which can consume variable-length key segments.
    """
    if not pats:
        if not keys:
            return []
        if partial:
            return []
        return None

    pop = pats[0]
    rest_pats = pats[1:]

    # Non-recursive op: consume exactly one key segment
    if not pop.is_recursive():
        if not keys:
            return None
        kop = keys[0]
        m = pop.match(kop, specials=True)
        if not m:
            return None
        rest_result = _match_ops(rest_pats, keys[1:], partial)
        if rest_result is None:
            return None
        if isinstance(m, (tuple, list)):
            return [_m.val for _m in m] + rest_result
        return [m.val] + rest_result

    # Recursive op: try consuming 1, 2, ... N key segments via backtracking
    for n in range(1, len(keys) + 1):
        kop = keys[n - 1]
        key_val = getattr(getattr(kop, 'op', kop), 'value', kop)
        matched = any(True for _ in pop.inner.matches((key_val,)))
        if not matched:
            break  # chain-following: stop extending once a segment fails
        rest_result = _match_ops(rest_pats, keys[n:], partial)
        if rest_result is not None:
            return list(keys[:n]) + rest_result
    return None


def match(pattern, key, groups=False, partial=True, strict=False):
    """
    Returns `key` if `pattern` matches; otherwise `None`
    >>> match('*.there', 'hello.there')
    'hello.there'
    >>> match('*', 'hello.there')
    'hello.there'
    >>> match('*.*', 'hello')
    >>> match('*', 'hello.there', partial=False)
    >>> match('*', 'hello.there', groups=True)
    ('hello.there', ('hello.there',))
    >>> match('*.*', 'hello.there', groups=True)
    ('hello.there', ('hello', 'there'))
    >>> match('hello', 'hello.there.bye', groups=True)
    ('hello.there.bye', ('hello.there.bye',))
    >>> match('hello.*', 'hello.there.bye', groups=True)
    ('hello.there.bye', ('hello', 'there.bye'))

    Recursive patterns:
    >>> match('**.c', 'a.b.c')
    'a.b.c'
    >>> match('*b', 'b.b.b')
    'b.b.b'
    >>> match('*b', 'a.b.c')
    """
    def returns(r, matches):
        return (r, tuple(matches)) if groups else r

    pats = parse(pattern)
    keys = parse(key)

    # Check if any pattern op is Recursive — use new recursive matcher
    has_recursive = any(op.is_recursive() for op in pats)

    if has_recursive:
        result = _match_ops(list(pats), list(keys), partial)
        if result is None:
            return returns(None, [])
        return returns(key, result)

    # Original non-recursive match logic
    _matches = []
    for idx,(pop,kop) in enumerate(zip(pats, keys)):
        # this means we have more pattern constraints than key items
        if kop is None:
            return returns(None, [])
        if pop is None:
            break
        m = pop.match(kop, specials=True)
        if not m:
            return returns(None, [])
        if isinstance(m, (tuple, list)):
            _matches.extend(_m.val for _m in m)
        else:
            _matches.append(m.val)

    # we've completed matching but the last item in match groups is treated 'greedily'
    assert kop is not None          # sanity

    # exact match
    if len(pats) == len(keys):
        return returns(key, _matches)

    # if we're not doing partial matches or we haven't consumed all pats, fail
    if not partial or idx < len(pats) - 1:
        return returns(None, [])

    # otherwise inexact (partial) match
    # assemble remaining keys
    rkey = keys.assemble(start=idx)
    if pop is None:
        _matches.append(rkey)
    else:
        _matches[-1] = rkey
    return returns(key, _matches)


def match_multi(pattern, iterable, groups=False, partial=True, strict=False):
    """
    Match pattern to all in iterable; returns iterable
    >>> list(match_multi('/h.*/', ['hello', 'there', 'hi']))
    ['hello', 'hi']
    """
    matches = (match(pattern, k, groups=groups, partial=partial, strict=strict) for k in iterable)
    if groups:
        return (m for m in matches if m[0])
    return (m for m in matches if m)


def overlaps(a, b):
    """
    Return True if paths a and b overlap — i.e. one is a prefix of the other.

    Accepts strings, pre-parsed Dotted objects, or op tuples.
    >>> overlaps('a', 'a.b.c')
    True
    >>> overlaps('a.b.c', 'a')
    True
    >>> overlaps('a.b', 'a.b')
    True
    >>> overlaps('a.b', 'a.c')
    False
    >>> overlaps('a.b.c', 'a.b.d')
    False
    """
    a = parse(a)
    b = parse(b)
    return base.path_overlaps([a.ops], b.ops)


def assemble_multi(keys_list):
    """
    Given a list of a list of path segments assemble into a full dotted path
    >>> assemble_multi((['hello', 'there'], ['a', 1, 'c']))
    ('hello.there', 'a.1.c')
    """
    def _assemble(keys):
        keys = ([k] if isinstance(k, base.Op) else parse(str(k) if not isinstance(k, str) else k) for k in keys)
        iterable = itertools.chain.from_iterable(keys)
        return engine.assemble(iterable)
    return tuple(_assemble(keys) for keys in keys_list)


def assemble(keys):
    """
    Given a list of path segments assemble into a full dotted path
    >>> assemble(['hello', 'there'])
    'hello.there'
    >>> assemble(['hello', '[*]', 'there'])
    'hello[*].there'
    >>> assemble(['[0]', 'hello.there'])
    '[0].hello.there'
    >>> assemble([7, 'hello'])
    '7.hello'
    """
    return assemble_multi((keys,))[0]


def expand_multi(obj, patterns, strict=False):
    """
    Expand across a set of patterns
    >>> d = {'hello': {'there': [1, 2, 3]}, 'bye': 7, 9: 'nine', '9': 'not nine'}
    >>> expand_multi(d, ('hello', '*'))
    ('hello', 'bye', '9', "'9'")
    """
    seen = {}
    for pat in patterns:
        keys = (o.assemble() for o in engine.expands(parse(pat), obj, strict=strict))
        for found in keys:
            if found not in seen:
                seen[found] = None
    return tuple(seen)


def expand(obj, pattern, strict=False):
    """
    Return all keys that match `pattern` in `obj`
    >>> d = {'hello': {'there': [1, 2, 3]}, 'bye': 7, 9: 'nine', '9': 'not nine'}
    >>> expand(d, '*')
    ('hello', 'bye', '9', "'9'")
    >>> expand(d, '*.*')
    ('hello.there',)
    >>> expand(d, '*.*[*]')
    ('hello.there[0]', 'hello.there[1]', 'hello.there[2]')
    >>> expand(d, '*.*[1:]')
    ('hello.there[1:]',)
    """
    return expand_multi(obj, (pattern,), strict=strict)


def apply_multi(obj, patterns, strict=False):
    """
    Update `obj` with transforms at `patterns`
    >>> d = {'hello': 7, 'there': 9}
    >>> apply_multi(d, ('*|float', 'hello|str'))
    {'hello': '7.0', 'there': 9.0}
    """
    seen = {}
    _marker = object()
    for pat in patterns:
        for ops in engine.expands(parse(pat), obj, strict=strict):
            if ops in seen:
                continue
            seen[ops] = None
            first = next(engine.iter_until_cut(engine.gets(ops, obj, strict=strict)), _marker)
            if first is _marker:
                continue
            val = ops.apply(first)
            obj = engine.updates(ops, obj, val, strict=strict)
    return obj


def apply(obj, pattern, strict=False):
    """
    Update `obj` with transforms at `pattern`
    >>> d = {'hello': 7}
    >>> apply(d, 'hello|str')
    {'hello': '7'}
    """
    return apply_multi(obj, (pattern,), strict=strict)


def pluck_multi(obj, patterns, default=None, strict=False):
    """
    Return the concrete field,value pairs from obj given patterns
    >>> d = {'hello': 7, 'there': 9, 'a': {'b': 'seven'}}
    >>> pluck_multi(d, ('hello', 'a.b'))
    (('hello', 7), ('a.b', 'seven'))
    """
    out = ()
    seen = {}
    for pattern in patterns:
        ops = parse(pattern)
        for path, val in engine.walk(ops, obj, paths=True, strict=strict):
            if path is utypes.CUT_SENTINEL:
                break
            field = engine.Dotted({'ops': path, 'transforms': ops.transforms}).assemble()
            if field in seen:
                continue
            seen[field] = None
            out += ((field, val),)
    return out


def pluck(obj, pattern, default=None, strict=False):
    """
    Return the concrete field,value pairs from obj given pattern
    >>> d = {'hello': 7, 'there': 9, 'a': {'b': 'seven', 'c': 'nine'}}
    >>> pluck(d, 'a.*')
    (('a.b', 'seven'), ('a.c', 'nine'))
    >>> pluck(d, 'a.b')
    ('a.b', 'seven')
    """
    out = pluck_multi(obj, (pattern,), default=default, strict=strict)
    if not out:
        return ()
    if is_pattern(pattern):
        return out
    return out[0]


def unpack(obj, attrs=None):
    """
    Convert obj to dotted normal form.  A tuple of (path, value) pairs which
    can be replayed to regenerate the obj.  Internally, this calls:
         pluck(obj, '*(*#, [*]:!(str, bytes)):-2(.*, [])##, (*, [])')

    Pass attrs= to include object attributes:
        attrs=[Attrs.standard]            non-dunder attrs
        attrs=[Attrs.special]             dunder attrs only
        attrs=[Attrs.standard, Attrs.special]  all attrs

    >>> d = {'a': {'b': [1, 2, 3]}, 'x': {'y': {'z': [4, 5]}}, 'extra': 'stuff'}
    >>> r = unpack(d)
    >>> r
    (('a.b', [1, 2, 3]), ('x.y.z', [4, 5]), ('extra', 'stuff'))
    >>> update_multi(AUTO, r) == d
    True
    """
    if not attrs:
        extra = ''
    elif set(attrs) >= {Attrs.standard, Attrs.special}:
        extra = ', @*'
    elif Attrs.standard in attrs:
        extra = ', @/(?!__).*/'
    else:
        extra = ', @/__.*/'
    return pluck(obj, f'*(*#, [*]:!(str, bytes){extra}):-2(.*, []{extra})##, (*, []{extra})')


#
# transform registry
#
def register(name, fn):
    """
    Register a transform at `name` to call `fn`
    """
    return engine.Dotted.register(name, fn)


def transform(name):
    """
    Decorator form of `register`

    >>> @transform('hello')
    ... def hello():
    ...     return 'hello'
    """
    return engine.transform(name)


def registry():
    return engine.Dotted._registry

registry.__doc__ = engine.Dotted.registry.__doc__
