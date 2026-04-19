"""
Tests for JSON-style sentinel aliases: true/false/null alongside True/False/None.
"""
import dotted


def test_parse_true_alias():
    p = dotted.parse('active=true')
    assert dotted.assemble(p) == 'active=True'


def test_parse_false_alias():
    p = dotted.parse('enabled=false')
    assert dotted.assemble(p) == 'enabled=False'


def test_parse_null_alias():
    p = dotted.parse('x=null')
    assert dotted.assemble(p) == 'x=None'


def test_value_guard_true():
    assert dotted.get({'x': True}, 'x=true') is True
    assert dotted.get({'x': False}, 'x=true') is None


def test_value_guard_false():
    assert dotted.get({'x': False}, 'x=false') is False
    assert dotted.get({'x': True}, 'x=false') is None


def test_value_guard_null():
    assert dotted.get({'x': None}, 'x=null') is None
    assert dotted.get({'x': 0}, 'x=null') is None


def test_filter_true_alias():
    data = [{'active': True}, {'active': False}]
    assert dotted.get(data, '[active=true]') == [{'active': True}]


def test_filter_false_alias():
    data = [{'active': True}, {'active': False}]
    assert dotted.get(data, '[active=false]') == [{'active': False}]


def test_filter_null_alias():
    data = [{'x': 1}, {'x': None}]
    assert dotted.get(data, '[x=null]') == [{'x': None}]


def test_aliases_equivalent_to_canonical():
    data = [{'a': True, 'b': False, 'c': None}]
    assert dotted.get(data, '[a=true]') == dotted.get(data, '[a=True]')
    assert dotted.get(data, '[b=false]') == dotted.get(data, '[b=False]')
    assert dotted.get(data, '[c=null]') == dotted.get(data, '[c=None]')


def test_neq_aliases():
    data = [{'a': True}, {'a': False}, {'a': None}]
    assert dotted.get(data, '[a!=true]') == [{'a': False}, {'a': None}]
    assert dotted.get(data, '[a!=null]') == [{'a': True}, {'a': False}]
