"""
Main api
"""
import copy
import functools
import itertools
from . import grammar
from . import elements as el

CACHE_SIZE = 300
ANY = el.ANY


class ParseError(Exception):
    """Raised when dotted notation cannot be parsed."""
    pass


@functools.lru_cache(CACHE_SIZE)
def _parse(ops):
    try:
        results = grammar.template.parse_string(ops, parse_all=True)
    except el.pp.ParseException as e:
        raise ParseError(f"Invalid dotted notation: {e.msg}\n  {repr(ops)}\n  {' ' * e.loc}^") from None
    return el.Dotted(results)


def parse(key):
    """
    Parse dotted notation
    >>> parse('hello.there|str:"=%s"')
    Dotted([hello, there], [('str', '=%s')])
    """
    if isinstance(key, el.Dotted):
        return key
    return _parse(key)


def quote(key, as_key=True):
    """
    How to quote a key
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
    "'7'"
    """
    return el.quote(key, as_key=as_key)


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
    if isinstance(key, el.Dotted):
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
    return isinstance(ops[0], el.Invert)


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


def mutable(obj, key):
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
    if len(ops) == 1 and isinstance(ops[0], el.Empty):
        return False

    # Walk the path - if we find any mutable container, mutation will occur
    current = obj
    for op in ops:
        if isinstance(op, el.Invert):
            continue

        if _is_mutable_container(current):
            return True

        # Traverse to next level
        vals = list(op.values(current))
        if not vals:
            # Path doesn't exist - would be created, but parent is immutable
            return False
        current = vals[0]

    return False


# Alias for use inside functions where 'mutable' parameter shadows the function
_mutable = mutable


def build_multi(obj, keys):
    """
    Build a subset/default obj based on concrete key fields
    >>> build_multi({}, ('hello.bye[]', 'hello.there', ))
    {'hello': {'bye': [], 'there': None}}
    """
    for key in keys:
        built = el.build(parse(key), obj)
        obj = update_multi(obj, pluck_multi(built, (key,)))
    return obj


def build(obj, key):
    """
    Build a subset/default obj based on dotted
    >>> build({}, 'hello.there')
    {'hello': {'there': None}}
    >>> # build({}, 'a.b.c[:2].d')
    """
    return build_multi(obj, (key,))


def get(obj, key, default=None, pattern_default=(), apply_transforms=True):
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
    """
    ops = parse(key)
    vals = el.iter_until_cut(el.gets(ops, obj))
    if apply_transforms:
        vals = ( ops.apply(v) for v in vals )
    found = tuple(vals)
    if not is_pattern(ops):
        return found[0] if found else default
    return found if found else pattern_default


def get_multi(obj, iterable, apply_transforms=True):
    """
    Get all values from obj in iterable; return iterable
    >>> list(get_multi({'hello': 7, 'there': 9}, ['hello', 'there']))
    [7, 9]
    """
    dummy = object()
    found = (get(obj, k, dummy, dummy, apply_transforms=apply_transforms) for k in iterable)
    return (v for v in found if v is not dummy)


def has(obj, key):
    """
    True if key/pattern is contained in obj
    >>> d = {'hello': {'there': [1, '2', 3]}}
    >>> has(d, 'hello.*')
    True
    >>> has(d, 'hello.bye')
    False
    """
    dummy = object()
    return get(obj, key, dummy, dummy) is not dummy


def setdefault(obj, key, val, apply_transforms=True):
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
    if has(obj, key):
        return  get(obj, key, apply_transforms=apply_transforms)
    obj = update(obj, key, val, apply_transforms=apply_transforms)
    return get(obj, key, apply_transforms=False)


def setdefault_multi(obj, keyvalues, apply_transforms=True):
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
        yield setdefault(obj, k, v, apply_transforms=apply_transforms)


def update_if(obj, key, val, pred=lambda val: val is None, mutable=True, apply_transforms=True):
    """
    Update when the path is missing or when pred(current_value) is true.
    Always updates when there is nothing at the key (path missing); the predicate
    only gates updates when the path exists. Default pred is lambda val: val is None,
    so by default we fill missing or None slots and do not overwrite existing non-None.

    Equivalent using path expressions only (no pred): update_if with default pred
    is the same as update with path "( (name&first=None).first, name.~first, name.first )?"
    (if first is None -> update; if first exists with value -> NOP; if missing -> create).
    Note that path expressions can use the ``~`` operator to match but not update (NOP).
    >>> path = '( (name&first=None).first, name.~first, name.first )?'
    >>> update_if({'name': {}}, 'name.first', 'hello')
    {'name': {'first': 'hello'}}
    >>> update({'name': {}}, path, 'hello')
    {'name': {'first': 'hello'}}
    >>> update_if({'name': {'first': 'Alice'}}, 'name.first', 'hello')
    {'name': {'first': 'Alice'}}
    >>> update({'name': {'first': 'Alice'}}, path, 'hello')
    {'name': {'first': 'Alice'}}
    >>> update_if({'name': {'first': None}}, 'name.first', 'hello')
    {'name': {'first': 'hello'}}
    >>> update({'name': {'first': None}}, path, 'hello')
    {'name': {'first': 'hello'}}
    """
    dummy = object()
    if not mutable and _is_mutable_container(obj):
        obj = copy.deepcopy(obj)
        mutable = True

    if pred is None:
        ops = parse(key)
        return el.updates(ops, obj, ops.apply(val) if apply_transforms else val)

    if not is_pattern(key):
        ops = parse(key)
        current = get(obj, key, dummy, dummy, apply_transforms=False)
        if current is dummy or pred(current):
            obj = el.updates(ops, obj, ops.apply(val) if apply_transforms else val)
        return obj

    paths = expand(obj, key)
    for path in paths:
        ops = parse(path)
        current = get(obj, ops, dummy, dummy, apply_transforms=False)
        if current is dummy or pred(current):
            obj = el.updates(ops, obj, ops.apply(val) if apply_transforms else val)
    return obj


def update_if_multi(obj, items, pred=lambda val: val is None, mutable=True, apply_transforms=True):
    """
    Update multiple keys with per-item or default predicate. items: iterable of
    (key, val) or (key, val, pred). (key, val) uses the method pred; (key, val, p)
    uses p when p is not None else method pred.
    >>> update_if_multi({'a': 1}, [('a', 99, lambda v: v == 1), ('b', 2)])
    {'a': 99, 'b': 2}
    """
    if not mutable and _is_mutable_container(obj):
        obj = copy.deepcopy(obj)
        mutable = True
    for item in items:
        key, val, *rest = item
        p = rest[0] if rest else pred
        obj = update_if(obj, key, val, pred=p, mutable=mutable, apply_transforms=apply_transforms)
    return obj


def update(obj, key, val, mutable=True, apply_transforms=True):
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

    Use mutable=False to prevent mutation of the original object:
    >>> d = {'a': 1}
    >>> result = update(d, 'a', 2, mutable=False)
    >>> d
    {'a': 1}
    >>> result
    {'a': 2}
    """
    return update_if(obj, key, val, pred=None, mutable=mutable, apply_transforms=apply_transforms)


def update_multi(obj, keyvalues, mutable=True, apply_transforms=True):
    """
    Update obj with all keyvalues
    >>> update_multi({}, [('hello.there', 7), ('my.my', 9)])
    {'hello': {'there': 7}, 'my': {'my': 9}}
    >>> update_multi({}, {'stuff.more.stuff': 'mine'})
    {'stuff': {'more': {'stuff': 'mine'}}}
    """
    if hasattr(keyvalues, 'items') and callable(keyvalues.items):
        keyvalues = keyvalues.items()
    return update_if_multi(obj, keyvalues, pred=None, mutable=mutable, apply_transforms=apply_transforms)


def remove_if(obj, key, pred=lambda val: val is None, val=ANY, mutable=True):
    """
    Remove when the path is missing or when pred(current_value) is true.
    Always removes when there is nothing at the key; the predicate only gates
    removal when the path exists. Default pred is lambda val: val is None.
    >>> remove_if({'a': 1, 'b': None}, 'b')
    {'a': 1}
    >>> remove_if({'a': 1, 'b': 2}, 'b')
    {'a': 1, 'b': 2}
    """
    if not mutable and _is_mutable_container(obj):
        obj = copy.deepcopy(obj)
        mutable = True

    if pred is None:
        return el.removes(parse(key), obj, val)

    dummy = object()
    if not is_pattern(key):
        ops = parse(key)
        current = get(obj, key, dummy, dummy, apply_transforms=False)
        if current is dummy or pred(current):
            return el.removes(ops, obj, val)
        return obj

    paths = expand(obj, key)
    for path in paths:
        ops = parse(path)
        current = get(obj, ops, dummy, dummy, apply_transforms=False)
        if current is dummy or pred(current):
            obj = el.removes(ops, obj, val)
    return obj


def remove_if_multi(obj, items, keys_only=True, pred=lambda val: val is None, mutable=True):
    """
    Remove by keys or (key, val, pred). When keys_only=True,
    items is an iterable of keys and pred is used for each (default: remove when
    value is None). When keys_only=False, items is (key, val) or (key, val, pred),
    matching update_if_multi's (key, val, pred?) arrangement.
    >>> remove_if_multi({'a': 1, 'b': None, 'c': 2}, ['b'])
    {'a': 1, 'c': 2}
    """
    if not mutable and _is_mutable_container(obj):
        obj = copy.deepcopy(obj)
        mutable = True

    if keys_only:
        for k in items:
            obj = remove_if(obj, k, pred=pred, val=ANY, mutable=mutable)
        return obj

    for item in items:
        k, v, *rest = item
        p = rest[0] if rest else pred
        obj = remove_if(obj, k, pred=p, val=v, mutable=mutable)
    return obj


def remove(obj, key, val=ANY, mutable=True):
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

    Use mutable=False to prevent mutation of the original object:
    >>> d = {'a': 1, 'b': 2}
    >>> result = remove(d, 'a', mutable=False)
    >>> d
    {'a': 1, 'b': 2}
    >>> result
    {'b': 2}
    """
    return remove_if(obj, key, pred=None, val=val, mutable=mutable)


def remove_multi(obj, iterable, keys_only=True, mutable=True):
    """
    Remove by keys or key-values
    >>> remove_multi({'hello': {'there': 7}, 'my': {'precious': 9}}, ['hello', 'my.precious'])
    {'my': {}}
    """
    if keys_only:
        return remove_if_multi(obj, iterable, keys_only=True, pred=None, mutable=mutable)
    if hasattr(iterable, 'items') and callable(iterable.items):
        iterable = iterable.items()
    return remove_if_multi(obj, iterable, keys_only=False, pred=None, mutable=mutable)


def match(pattern, key, groups=False, partial=True):
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
    """
    def returns(r, matches):
        return (r, tuple(matches)) if groups else r

    _matches = []
    pats = parse(pattern)
    keys = parse(key)
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


def match_multi(pattern, iterable, groups=False, partial=True):
    """
    Match pattern to all in iterable; returns iterable
    >>> list(match_multi('/h.*/', ['hello', 'there', 'hi']))
    ['hello', 'hi']
    """
    matches = (match(pattern, k, groups=groups, partial=partial) for k in iterable)
    if groups:
        return (m for m in matches if m[0])
    return (m for m in matches if m)


def assemble_multi(keys_list):
    """
    Given a list of a list of keys assemble into a full dotted string
    >>> assemble_multi((['hello', 'there'], ['a', 1, 'c']))
    ('hello.there', 'a.1.c')
    """
    def _assemble(keys):
        keys = ([k] if isinstance(k, el.Op) else parse(quote(k)) for k in keys)
        iterable = itertools.chain.from_iterable(keys)
        return el.assemble(iterable)
    return tuple(_assemble(keys) for keys in keys_list)


def assemble(keys):
    """
    Given a list of keys assemble into a full dotted string
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


def expand_multi(obj, patterns):
    """
    Expand across a set of patterns
    >>> d = {'hello': {'there': [1, 2, 3]}, 'bye': 7, 9: 'nine', '9': 'not nine'}
    >>> expand_multi(d, ('hello', '*'))
    ('hello', 'bye', '9', "'9'")
    """
    seen = {}
    for pat in patterns:
        keys = (o.assemble() for o in el.expands(parse(pat), obj))
        for found in keys:
            if found not in seen:
                seen[found] = None
    return tuple(seen)


def expand(obj, pattern):
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
    return expand_multi(obj, (pattern,))


def apply_multi(obj, patterns):
    """
    Update `obj` with transforms at `patterns`
    >>> d = {'hello': 7, 'there': 9}
    >>> apply_multi(d, ('*|float', 'hello|str'))
    {'hello': '7.0', 'there': 9.0}
    """
    seen = {}
    for pat in patterns:
        for ops in el.expands(parse(pat), obj):
            if ops in seen:
                continue
            seen[ops] = None
            vals = tuple(el.iter_until_cut(el.gets(ops, obj)))
            if not vals:
                continue
            val = ops.apply(vals[0])
            obj = el.updates(ops, obj, val)
    return obj


def apply(obj, pattern):
    """
    Update `obj` with transforms at `pattern`
    >>> d = {'hello': 7}
    >>> apply(d, 'hello|str')
    {'hello': '7'}
    """
    return apply_multi(obj, (pattern,))


def pluck_multi(obj, patterns, default=None):
    """
    Return the concrete field,value pairs from obj given patterns
    >>> d = {'hello': 7, 'there': 9, 'a': {'b': 'seven'}}
    >>> pluck_multi(d, ('hello', 'a.b'))
    (('hello', 7), ('a.b', 'seven'))
    """
    out = ()
    for field in expand_multi(obj, patterns):
        out += ((field, get(obj, field, default=default)),)
    return out


def pluck(obj, pattern, default=None):
    """
    Return the concrete field,value pairs from obj given pattern
    >>> d = {'hello': 7, 'there': 9, 'a': {'b': 'seven', 'c': 'nine'}}
    >>> pluck(d, 'a.*')
    (('a.b', 'seven'), ('a.c', 'nine'))
    >>> pluck(d, 'a.b')
    ('a.b', 'seven')
    """
    out = pluck_multi(obj, (pattern,), default=default)
    if not out:
        return ()
    if is_pattern(pattern):
        return out
    return out[0]


#
# transform registry
#
def register(name, fn):
    """
    Register a transform at `name` to call `fn`
    """
    return el.Dotted.register(name, fn)


def transform(name):
    """
    Decorator form of `register`

    >>> @transform('hello')
    ... def hello():
    ...     return 'hello'
    """
    return el.transform(name)


def registry():
    return el.Dotted._registry

registry.__doc__ = el.Dotted.registry.__doc__
