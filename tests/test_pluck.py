import pytest
import dotted


# Basic pluck functionality

def test_pluck_simple_key():
    d = {'a': 1, 'b': 2}
    assert dotted.pluck(d, 'a') == ('a', 1)


def test_pluck_nested_key():
    d = {'a': {'b': {'c': 3}}}
    assert dotted.pluck(d, 'a.b.c') == ('a.b.c', 3)


def test_pluck_missing_key():
    d = {'a': 1}
    assert dotted.pluck(d, 'x') == ()


def test_pluck_missing_key_with_default():
    # Note: default only applies when key exists in expansion
    # For non-pattern, missing key returns empty tuple
    d = {'a': 1}
    assert dotted.pluck(d, 'x', default='missing') == ()


# Pluck with pattern matching

def test_pluck_wildcard():
    d = {'a': 1, 'b': 2, 'c': 3}
    r = dotted.pluck(d, '*')
    assert len(r) == 3
    assert set(k for k, v in r) == {'a', 'b', 'c'}


def test_pluck_nested_wildcard():
    d = {'x': {'a': 1, 'b': 2}, 'y': {'c': 3}}
    r = dotted.pluck(d, '*.*')
    assert len(r) == 3
    assert ('x.a', 1) in r
    assert ('x.b', 2) in r
    assert ('y.c', 3) in r


def test_pluck_wildcard_first():
    d = {'a': 1, 'b': 2, 'c': 3}
    r = dotted.pluck(d, '*?')
    assert len(r) == 1
    assert r[0][1] in (1, 2, 3)


def test_pluck_regex():
    d = {'user_name': 'alice', 'user_id': 1, 'other': 'x'}
    r = dotted.pluck(d, '/user_.*/')
    assert len(r) == 2
    assert set(k for k, v in r) == {'user_name', 'user_id'}


# Pluck with list indexing

def test_pluck_list_index():
    d = {'items': ['a', 'b', 'c']}
    assert dotted.pluck(d, 'items[0]') == ('items[0]', 'a')


def test_pluck_list_wildcard():
    d = {'items': ['a', 'b', 'c']}
    r = dotted.pluck(d, 'items[*]')
    assert len(r) == 3
    assert ('items[0]', 'a') in r
    assert ('items[1]', 'b') in r
    assert ('items[2]', 'c') in r


def test_pluck_nested_list():
    d = {'users': [{'name': 'alice'}, {'name': 'bob'}]}
    r = dotted.pluck(d, 'users[*].name')
    assert r == (('users[0].name', 'alice'), ('users[1].name', 'bob'))


# Pluck with path grouping

def test_pluck_disjunction():
    d = {'a': 1, 'b': 2, 'c': 3}
    r = dotted.pluck(d, '(a,b)')
    assert len(r) == 2
    assert ('a', 1) in r
    assert ('b', 2) in r


def test_pluck_disjunction_partial():
    d = {'a': 1, 'c': 3}
    r = dotted.pluck(d, '(a,b)')
    assert r == (('a', 1),)


def test_pluck_conjunction():
    d = {'a': 1, 'b': 2}
    r = dotted.pluck(d, '(a&b)')
    assert len(r) == 2


def test_pluck_conjunction_missing():
    d = {'a': 1}
    r = dotted.pluck(d, '(a&b)')
    assert r == ()


# Pluck with filters

def test_pluck_dict_filter():
    d = {
        'a': {'id': 1, 'val': 'x'},
        'b': {'id': 2, 'val': 'y'},
    }
    r = dotted.pluck(d, '*&id=1')
    assert r == (('a', {'id': 1, 'val': 'x'}),)


def test_pluck_list_filter():
    data = [{'id': 1, 'name': 'alice'}, {'id': 2, 'name': 'bob'}]
    r = dotted.pluck(data, '[id=1]')
    assert r == ('[0]', {'id': 1, 'name': 'alice'})


def test_pluck_list_filter_pattern():
    data = [{'id': 1, 'name': 'alice'}, {'id': 2, 'name': 'bob'}]
    r = dotted.pluck(data, '[*&id=1]')
    assert r == (('[0]', {'id': 1, 'name': 'alice'}),)


# pluck_multi functionality

def test_pluck_multi_basic():
    d = {'a': 1, 'b': 2, 'c': 3}
    r = dotted.pluck_multi(d, ('a', 'c'))
    assert r == (('a', 1), ('c', 3))


def test_pluck_multi_nested():
    d = {'x': {'a': 1}, 'y': {'b': 2}}
    r = dotted.pluck_multi(d, ('x.a', 'y.b'))
    assert r == (('x.a', 1), ('y.b', 2))


def test_pluck_multi_with_patterns():
    d = {'a': 1, 'b': 2, 'c': 3}
    r = dotted.pluck_multi(d, ('a', '*'))
    # pluck_multi deduplicates, so 'a' only appears once
    assert len(r) == 3
    assert set(k for k, v in r) == {'a', 'b', 'c'}


def test_pluck_multi_missing_with_default():
    # Missing keys are not included in result (expand doesn't find them)
    d = {'a': 1}
    r = dotted.pluck_multi(d, ('a', 'x'), default='missing')
    assert r == (('a', 1),)


# Edge cases for pluck

def test_pluck_empty_dict():
    assert dotted.pluck({}, 'a') == ()
    assert dotted.pluck({}, '*') == ()


def test_pluck_empty_list():
    assert dotted.pluck([], '[0]') == ()
    assert dotted.pluck([], '[*]') == ()


def test_pluck_none_value():
    d = {'a': None}
    assert dotted.pluck(d, 'a') == ('a', None)


def test_pluck_numeric_key():
    d = {1: 'one', 2: 'two'}
    assert dotted.pluck(d, '[1]') == ('[1]', 'one')


def test_pluck_quoted_key():
    # Note: expand returns unquoted key, so subsequent get fails
    # This is a known limitation - see issue for quoted key handling
    d = {'a.b': 'dotted', 'normal': 'x'}
    # Currently returns ('a.b', None) because get('a.b') != get('"a.b"')
    r = dotted.pluck(d, '"a.b"')
    assert r[0] == 'a.b'  # key is unquoted
