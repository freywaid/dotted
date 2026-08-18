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


def test_group_or():
    assert dotted.match('(a,b)', 'a') == 'a'
    assert dotted.match('(a,b)', 'b') == 'b'
    assert dotted.match('(a,b)', 'c') is None


def test_group_first():
    assert dotted.match('(a,b)?', 'a') == 'a'
    assert dotted.match('(a,b)?', 'b') == 'b'
    assert dotted.match('(a,b)?', 'c') is None


def test_group_and():
    assert dotted.match('(a&b)', 'a') == 'a'
    assert dotted.match('(a&b)', 'b') == 'b'
    assert dotted.match('(a&b)', 'c') is None
    assert dotted.match('x(.a&.b)', 'x.a') == 'x.a'


def test_group_not():
    assert dotted.match('(!a)', 'b') == 'b'
    assert dotted.match('(!a)', 'a') is None
    assert dotted.match('(!a).y', 'b.y') == 'b.y'
    assert dotted.match('(!a).y', 'a.y') is None


def test_group_mid_path():
    assert dotted.match('x.(a,b)', 'x.a') == 'x.a'
    assert dotted.match('x.(a,b)', 'x.c') is None
    assert dotted.match('(a,b).y', 'a.y') == 'a.y'
    assert dotted.match('(a,b).y', 'a.z') is None


def test_group_multi_segment_branch():
    assert dotted.match('(a.b,c)', 'a.b') == 'a.b'
    assert dotted.match('(a.b,c)', 'c') == 'c'
    assert dotted.match('(a.b,c)', 'a') is None


def test_group_nested():
    assert dotted.match('((a,b),c)', 'b') == 'b'


def test_group_cuts_ignored():
    assert dotted.match('(a#,b)', 'b') == 'b'
    assert dotted.match('(a##,b)', 'b') == 'b'


def test_group_recursive_branch():
    assert dotted.match('(*b,c)', 'b.b') == 'b.b'
    assert dotted.match('(*b,c)', 'c') == 'c'
    assert dotted.match('(*b,c)', 'a') is None


def test_group_partial():
    assert dotted.match('(a,b)', 'a.x') == 'a.x'
    assert dotted.match('(a,b)', 'a.x', partial=False) is None


def test_group_groups():
    assert dotted.match('(a,b)', 'a', groups=True) == ('a', ('a',))
    assert dotted.match('(!a)', 'b', groups=True) == ('b', ('b',))
    assert dotted.match('x.(a.b,c)', 'x.a.b', groups=True) == ('x.a.b', ('x', 'a.b'))


def test_group_groups_patterns_only():
    m, g = dotted.match('a.(x,y).*', 'a.x.z', groups='patterns', partial=False)
    assert m == 'a.x.z'
    assert g == ('x', 'z')

    m, g = dotted.match('x.(a.b,c)', 'x.a.b', groups='patterns')
    assert m == 'x.a.b'
    assert g == ('a.b',)


def test_recursive_groups_patterns_only():
    m, g = dotted.match('**.c', 'a.b.c', groups='patterns')
    assert m == 'a.b.c'
    assert g == ('a.b',)


def test_group_wrapped():
    assert dotted.match('~(a,b)', 'a') == 'a'
    assert dotted.match('~(a,b)', 'c') is None


def test_recursive_wrapped_value_guard():
    assert dotted.match('**=7', 'a.b') == 'a.b'
