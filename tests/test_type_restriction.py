"""
Tests for path segment type restrictions.
"""
import types
import pytest
import dotted
from dotted import elements as el


def _w():
    """
    Dummy inner op for direct TypeRestriction construction.
    """
    return el.Key(el.Wildcard())


# -- Round-trip: parse then assemble should preserve the type restriction --

@pytest.mark.parametrize('expr,expected', [
    ('*:dict', '*:dict'),
    ('*:list', '*:list'),
    ('*:!(str)', '*:!str'),
    ('*:!str', '*:!str'),
    ('*:!(str, bytes)', '*:!(str, bytes)'),
    ('[*]:!(str, bytes)', '[*]:!(str, bytes)'),
    ('[*]:list', '[*]:list'),
    ('[0]:tuple', '[0]:tuple'),
    ('@name:dict', '@name:dict'),
    ('@*:!(bool)', '@*:!bool'),
    ('name:dict', 'name:dict'),
])
def test_round_trip(expr, expected):
    """
    Round-trip: parse then assemble should preserve the type restriction.
    """
    ops = dotted.api.parse(expr)
    assert ops.assemble() == expected


# -- TypeRestriction.allows() logic --

def test_allows_positive_single():
    """
    Positive single type: allows matching type, rejects others.
    """
    tr = el.TypeRestriction(_w(), dict)
    assert tr.allows({'a': 1})
    assert not tr.allows([1, 2])
    assert not tr.allows('hello')


def test_allows_positive_multiple():
    """
    Positive multiple types: allows any listed type.
    """
    tr = el.TypeRestriction(_w(), dict, list)
    assert tr.allows({'a': 1})
    assert tr.allows([1, 2])
    assert not tr.allows('hello')


def test_allows_negative_single():
    """
    Negated single type: rejects matching type, allows others.
    """
    tr = el.TypeRestriction(_w(), str, negate=True)
    assert tr.allows({'a': 1})
    assert tr.allows([1, 2])
    assert not tr.allows('hello')


def test_allows_negative_multiple():
    """
    Negated multiple types: rejects any listed type.
    """
    tr = el.TypeRestriction(_w(), str, bytes, negate=True)
    assert tr.allows({'a': 1})
    assert tr.allows([1, 2])
    assert not tr.allows('hello')
    assert not tr.allows(b'hello')


# -- TypeRestriction repr --

def test_repr_single():
    """
    Single positive type renders as inner:type.
    """
    tr = el.TypeRestriction(_w(), dict)
    assert repr(tr) == '*:dict'


def test_repr_single_negated():
    """
    Single negated type renders as inner:!type.
    """
    tr = el.TypeRestriction(_w(), str, negate=True)
    assert repr(tr) == '*:!str'


def test_repr_multiple():
    """
    Multiple positive types render as inner:(t1, t2).
    """
    tr = el.TypeRestriction(_w(), str, bytes)
    assert repr(tr) == '*:(str, bytes)'


def test_repr_multiple_negated():
    """
    Multiple negated types render as inner:!(t1, t2).
    """
    tr = el.TypeRestriction(_w(), str, bytes, negate=True)
    assert repr(tr) == '*:!(str, bytes)'


# -- Key access with type restrictions --

def test_key_wildcard_dict_only():
    """
    *:dict only matches on dict nodes.
    """
    d = {'a': {'b': 1}, 'c': [1, 2], 'd': 'hello'}
    result = list(dotted.pluck(d, '*:dict.*'))
    assert result == [('a.b', 1)]


def test_key_concrete_dict_only():
    """
    name:dict only accesses if node is a dict.
    """
    d = {'x': {'y': 1}}
    assert dotted.get(d, 'x:dict.y') == 1


def test_key_concrete_wrong_type():
    """
    name:list on a dict returns default.
    """
    d = {'x': 1}
    assert dotted.get(d, 'x:list', 'missing') == 'missing'


def test_key_wildcard_exclude_str():
    """
    *:!(str) skips string nodes.
    """
    d = {'a': 'hello', 'b': {'c': 1}}
    result = list(dotted.pluck(d, '*:!(str).*'))
    assert result == [('b.c', 1)]


# -- Slot access with type restrictions --

def test_slot_wildcard_exclude_str():
    """
    [*]:!(str) doesn't iterate over string characters.
    """
    d = {'name': 'hello', 'items': [10, 20]}
    result = list(dotted.pluck(d, '*[*]:!(str)'))
    assert ('items[0]', 10) in result
    assert ('items[1]', 20) in result
    # No character decomposition of 'hello'
    assert all(k.startswith('items') for k, v in result)


def test_slot_wildcard_exclude_str_bytes():
    """
    [*]:!(str, bytes) skips both strings and bytes.
    """
    d = {'s': 'abc', 'b': b'xyz', 'l': [1, 2]}
    result = list(dotted.pluck(d, '*[*]:!(str, bytes)'))
    assert result == [('l[0]', 1), ('l[1]', 2)]


def test_slot_list_only():
    """
    [0]:list only accesses if node is a list.
    """
    assert dotted.get([10, 20], '[0]:list') == 10


def test_slot_list_on_dict():
    """
    [0]:list on a dict returns default.
    """
    d = {0: 'zero'}
    assert dotted.get(d, '[0]:list', 'missing') == 'missing'


def test_slot_tuple_only():
    """
    [0]:tuple only accesses tuples.
    """
    assert dotted.get((10, 20), '[0]:tuple') == 10
    assert dotted.get([10, 20], '[0]:tuple', 'missing') == 'missing'


# -- Attr access with type restrictions --

def test_attr_exclude_type():
    """
    @*:!(dict) skips dict objects.
    """
    ns = types.SimpleNamespace(x=1, y=2)
    d = {'x': 10, 'y': 20}
    # On namespace: should work
    result = list(dotted.pluck(ns, '@*:!(dict)'))
    assert ('@x', 1) in result
    # On dict: should skip (dict excluded)
    result2 = list(dotted.pluck(d, '@*:!(dict)'))
    assert result2 == []


# -- Type restrictions combined with filters --

def test_key_type_with_filter():
    """
    *:dict with a filter still works.
    """
    d = {'a': {'x': 1, 'y': 2}, 'b': {'x': 3, 'y': 4}}
    result = list(dotted.pluck(d, '*:dict&x=1.*'))
    assert result == [('a.x', 1), ('a.y', 2)]


# -- Recursive backward compatibility --

def test_dstar_still_skips_str():
    """
    ** still skips str/bytes via _RECURSIVE_TERMINALS.
    """
    d = {'name': 'hello', 'nested': {'x': 1}}
    result = list(dotted.pluck(d, '**'))
    paths = [p for p, v in result]
    # 'hello' is a leaf, not decomposed
    assert 'name' in paths
    # Individual chars are NOT yielded
    assert 'name[0]' not in paths


def test_recursive_with_typed_accessors():
    """
    *(*:dict#, [*]:!(str, bytes)) recurses without string decomposition.
    """
    d = {'a': {'b': [1, 2]}, 'c': 'hello'}
    result = list(dotted.pluck(d, '*(*#, [*]:!(str, bytes))'))
    vals = [v for p, v in result]
    assert {'b': [1, 2]} in vals
    assert [1, 2] in vals
    assert 1 in vals
    assert 2 in vals
    assert 'hello' in vals
    # No character decomposition
    assert 'h' not in vals
