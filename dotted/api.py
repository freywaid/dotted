"""
Main api
"""
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
    return a tuple of all matches
    >>> d = {'hello': {'there': [1, '2', 3]}}
    >>> get(d, 'hello.there[1]|int')
    2
    >>> get(d, 'hello.there[1:]')
    ['2', 3]
    >>> get([{'a': 1}, {'a':2}], '[*].a')
    (1, 2)
    """
    ops = parse(key)
    vals = el.gets(ops, obj)
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
    Set value at key only if key does not exist; return obj
    >>> d = {'hello': 'there'}
    >>> setdefault(d, 'hello', 'world')
    {'hello': 'there'}
    >>> setdefault(d, 'bye', 'world')
    {'hello': 'there', 'bye': 'world'}
    >>> setdefault({}, 'a.b.c', 7)
    {'a': {'b': {'c': 7}}}
    """
    if has(obj, key):
        return obj
    return update(obj, key, val, apply_transforms=apply_transforms)


def setdefault_multi(obj, keyvalues, apply_transforms=True):
    """
    Set multiple values, only where keys do not exist
    >>> setdefault_multi({'a': 1}, [('a', 999), ('b', 2)])
    {'a': 1, 'b': 2}
    >>> setdefault_multi({'debug': True}, {'debug': False, 'timeout': 30})
    {'debug': True, 'timeout': 30}
    """
    for k, v in keyvalues.items() if hasattr(keyvalues, 'items') else keyvalues:
        obj = setdefault(obj, k, v, apply_transforms=apply_transforms)
    return obj


def update(obj, key, val, apply_transforms=True):
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
    """
    ops = parse(key)
    return el.updates(ops, obj, ops.apply(val) if apply_transforms else val)


def update_multi(obj, keyvalues, apply_transforms=True):
    """
    Update obj with all keyvalus
    >>> update_multi({}, [('hello.there', 7), ('my.my', 9)])
    {'hello': {'there': 7}, 'my': {'my': 9}}
    >>> update_multi({}, {'stuff.more.stuff': 'mine'})
    {'stuff': {'more': {'stuff': 'mine'}}}
    """
    for k,v in keyvalues.items() if hasattr(keyvalues, 'items') else keyvalues:
        obj = update(obj, k, v, apply_transforms=apply_transforms)
    return obj


def remove(obj, key, val=ANY):
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
    """
    return el.removes(parse(key), obj, val)


def remove_multi(obj, iterable, keys_only=True):
    """
    Remove by keys or key-values
    >>> remove_multi({'hello': {'there': 7}, 'my': {'precious': 9}}, ['hello', 'my.precious'])
    {'my': {}}
    """
    if keys_only:
        iterable = ((k,ANY) for k in iterable)
    elif hasattr(iterable, 'items') and callable(iterable.items):
        iterable = iterable.items()
    for k,v in iterable:
        obj = remove(obj, k, v)
    return obj


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
            vals = tuple(el.gets(ops, obj))
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
