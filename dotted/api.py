"""
Main api
"""
import functools
import itertools
from . import grammar
from . import elements as el

CACHE_SIZE = 300
ANY = el.ANY


@functools.lru_cache(CACHE_SIZE)
def _parse(ops):
    results = grammar.template.parseString(ops, parseAll=True)
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


def build(obj, key):
    """
    Build a subset/default obj based on dotted
    >>> build({}, 'hello.there')
    {'hello': {'there': None}}
    >>> # build({}, 'a.b.c[:2].d')
    """
    return el.build(parse(key), obj)


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


def expand(obj, pattern):
    """
    Return all keys that match `pattern` in `obj`
    >>> d = {'hello': {'there': [1, 2, 3]}, 'bye': 7}
    >>> expand(d, '*')
    ('hello', 'bye')
    >>> expand(d, '*.*')
    ('hello.there',)
    >>> expand(d, '*.*[*]')
    ('hello.there[0]', 'hello.there[1]', 'hello.there[2]')
    >>> expand(d, '*.*[1:]')
    ('hello.there[1:]',)
    """
    ops = parse(pattern)
    return tuple(o.assemble() for o in el.expands(ops, obj))


def assemble(keys):
    """
    Given a list of keys assemble into a full dotted string
    >>> assemble(['hello', 'there'])
    'hello.there'
    >>> assemble(['hello', '[*]', 'there'])
    'hello[*].there'
    >>> assemble(['[0]', 'hello.there'])
    '[0].hello.there'
    """
    iterable = itertools.chain.from_iterable(( parse(key) for key in keys ))
    return el.assemble(iterable)


def apply(obj, key):
    """
    Update `obj` with transforms at `key`
    >>> d = {'hello': 7}
    >>> apply(d, 'hello|str')
    {'hello': '7'}
    """
    for ops in el.expands(parse(key), obj):
        vals = tuple(el.gets(ops, obj))
        if not vals:
            continue
        val = ops.apply(vals[0])
        obj = el.updates(ops, obj, val)
    return obj


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
