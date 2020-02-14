"""
"""
import functools
from . import grammar
from . import elements as el

@functools.lru_cache()
def _parse(ops):
    return el.Dotted(grammar.template.parseString(ops, parseAll=True).asList())

def parse(key):
    """
    Parse dotted notation
    >>> parse('hello.there')
    Dotted([hello, there])
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
    """
    return el.build(parse(key), obj)

def get(obj, key, default=None, pattern_default=()):
    """
    Get a value specified by the dotted key. If dotted is a pattern,
    return a tuple of all matches
    >>> d = {'hello': {'there': [1, 2, 3]}}
    >>> get(d, 'hello.there[1]')
    2
    """
    ops = parse(key)
    found = tuple(el.gets(ops, obj))
    if not is_pattern(ops):
        return found[0] if found else default
    return found if found else pattern_default

def update(obj, key, val):
    """
    Update obj with all matches to dotted key with val
    >>> d = {'hello': {'there': {'stuff': 1}}}
    >>> update(d, 'hello.there.stuff', 2)
    {'hello': {'there': {'stuff': 2}}}
    """
    el.updates(parse(key), obj, val)
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

if __name__ == '__main__':
    import doctest
    doctest.testmod()
