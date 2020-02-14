"""
"""
import functools
from . import grammar
from . import elements as el

@functools.lru_cache()
def _parse(dotted):
    return el.Dotted(grammar.template.parseString(dotted, parseAll=True).asList())

@functools.lru_cache()
def is_pattern(dotted):
    def _is_pattern(ops):
        if not ops:
            return False
        if ops[0].is_pattern():
            return True
        return _is_pattern(ops[1:])
    return _is_pattern(dotted)

def parse(dotted):
    """
    Parse dotted notation
    >>> parse('hello.there')
    Dotted([hello, there])
    """
    if isinstance(dotted, el.Dotted):
        return dotted
    return _parse(dotted)

def build(obj, dotted):
    """
    Build a subset/default obj based on dotted
    >>> build({}, 'hello.there')
    {'hello': {'there': None}}
    """
    m = parse(dotted)
    return el.build(m, obj)

def get(obj, dotted, default=None, pattern_default=()):
    """
    Get a value specified by the dotted key. If dotted is a pattern,
    return a tuple of all matches
    >>> d = {'hello': {'there': {'stuff': 1}}}
    >>> get(d, 'hello.there.stuff')
    1
    """
    m = parse(dotted)
    found = tuple(el.gets(m, obj))
    if not is_pattern(m):
        return found[0] if found else default
    return found if found else pattern_default

def update(obj, dotted, val):
    """
    Update obj with all matches to dotted key with val
    >>> d = {'hello': {'there': {'stuff': 1}}}
    >>> update(d, 'hello.there.stuff', 2)
    {'hello': {'there': {'stuff': 2}}}
    """
    m = parse(dotted)
    el.updates(m, obj, val)
    return obj

def remove(obj, dotted):
    """
    Remove all matches to dotted key from obj
    >>> d = {'hello': {'there': {'stuff': 1}}}
    >>> remove(d, 'hello.there.stuff')
    {'hello': {'there': {}}}
    """
    m = parse(dotted)
    el.removes(m, obj)
    return obj

if __name__ == '__main__':
    import doctest
    doctest.testmod()
