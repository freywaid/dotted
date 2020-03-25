"""
Main api
"""
import functools
import itertools
from . import grammar
from . import elements as el

@functools.lru_cache()
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

@functools.lru_cache()
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
    """
    ops = parse(key)
    el.updates(ops, obj, ops.apply(val) if apply_transforms else val)
    return obj

def remove(obj, key):
    """
    Remove all matches to dotted key from obj
    >>> d = {'hello': {'there': [1, 2, 3]}}
    >>> remove(d, 'hello.there[-1]')
    {'hello': {'there': [1, 2]}}
    """
    el.removes(parse(key), obj)
    return obj

def match(pattern, key, partial=True):
    """
    Returns `key` if `pattern` matches; otherwise `None`
    >>> match('*.there', 'hello.there')
    'hello.there'
    >>> match('*', 'hello.there')
    'hello.there'
    >>> match('*', 'hello.there', False)
    """
    for pop,kop in itertools.zip_longest(parse(pattern), parse(key)):
        if pop is None:
            return key if partial else None
        if kop is None:
            return None
        if not pop.match(kop):
            return None
    return key

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
        el.updates(ops, obj, val)
    return obj

def register(name, fn):
    """
    Register transform `name` to call `fn`
    """
    el.Dotted.registry[name] = fn

def transform(name):
    """
    Transform registry decorator
    >>> @transform('hello')
    ... def hello():
    ...     return 'hello'
    """
    return el.transform(name)
