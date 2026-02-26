"""
"""
import dotted


def test_non_numeric_match():
    m = dotted.match('*', 'street1')
    assert m == 'street1'

    m = dotted.match('*', '0hello')
    assert m == '0hello'


def test_wildcard_partial():
    m = dotted.match('*', 'abc.def')
    assert m == 'abc.def'

    m = dotted.match('*.*', 'abc.def')
    assert m == 'abc.def'

    m = dotted.match('*.*.*', 'abc.def')
    assert m is None


def test_wilcard_groups():
    m,g = dotted.match('*', 'abc.def', groups=True)
    assert m == 'abc.def'
    assert g == ('abc.def',)

    m,g = dotted.match('*.*', 'abc.def', groups=True)
    assert m == 'abc.def'
    assert g == ('abc', 'def')

    m,g = dotted.match('*.*.*', 'abc.def', groups=True)
    assert m is None
    assert g == ()


def test_wilcard_full():
    m = dotted.match('*', 'abc.def', partial=False)
    assert m is None

    m = dotted.match('*.*', 'abc.def', partial=False)
    assert m == 'abc.def'

    m = dotted.match('*.*.*', 'abc.def', partial=False)
    assert m is None


def test_regex():
    m = dotted.match('/a.+/', 'abc.def')
    assert m == 'abc.def'

    m = dotted.match('/a.+/', 'abc.def', partial=False)
    assert m is None


def test_pattern_to_pattern():
    assert dotted.match('*', '*') == '*'
    assert dotted.match('*', '*.*') == '*.*'
    assert dotted.match('*.*', '*') is None
    assert dotted.match('*', '*?') == '*?'
    assert dotted.match('*?', '*') is None
    assert dotted.match('*', '/hello/') == '/hello/'
    assert dotted.match('*', '/hello/?') == '/hello/?'

    assert dotted.match('/.*/', '/hello/') == '/hello/'
    assert dotted.match('/.*/', '/hello/?') == '/hello/?'
    assert dotted.match('/.*/?', '/hello/') is None
    assert dotted.match('/.*/?', '/hello/?') == '/hello/?'

    assert dotted.match('*', '-*') is None
    assert dotted.match('-*', '*') is None
    assert dotted.match('-*', '-*') == '-*'


def test_recursive_groups_dstar():
    r = dotted.match('**.name', 'a.b.name', groups=True)
    assert r == ('a.b.name', ('a.b', 'name'))


def test_recursive_groups_star_key():
    r = dotted.match('*k', 'k.k.k', groups=True)
    assert r == ('k.k.k', ('k',))


def test_recursive_groups_with_continuation():
    r = dotted.match('**.c', 'a.b.c', groups=True)
    assert r == ('a.b.c', ('a.b', 'c'))


def test_recursive_groups_deep():
    r = dotted.match('**.x', 'a.b.c.x', groups=True)
    assert r == ('a.b.c.x', ('a.b.c', 'x'))
