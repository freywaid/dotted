"""
Tests for comparison predicate operators: <, >, <=, >=.
"""
import pytest
import dotted
from dotted.api import parse
from dotted.results import assemble


# ---------------------------------------------------------------------------
# Parse / assemble round-trip
# ---------------------------------------------------------------------------

def test_parse_roundtrip_lt():
    """
    key<7 round-trips through parse/assemble.
    """
    assert assemble(parse('key<7')) == 'key<7'


def test_parse_roundtrip_gt():
    """
    key>7 round-trips through parse/assemble.
    """
    assert assemble(parse('key>7')) == 'key>7'


def test_parse_roundtrip_le():
    """
    key<=7 round-trips through parse/assemble.
    """
    assert assemble(parse('key<=7')) == 'key<=7'


def test_parse_roundtrip_ge():
    """
    key>=7 round-trips through parse/assemble.
    """
    assert assemble(parse('key>=7')) == 'key>=7'


def test_parse_roundtrip_slot_le():
    """
    [*]<=10 round-trips.
    """
    assert assemble(parse('[*]<=10')) == '[*]<=10'


def test_parse_roundtrip_slot_gt():
    """
    [*]>5 round-trips.
    """
    assert assemble(parse('[*]>5')) == '[*]>5'


def test_parse_roundtrip_recursive_gt():
    """
    **>0 round-trips.
    """
    assert assemble(parse('**>0')) == '**>0'


def test_parse_roundtrip_recursive_lt():
    """
    **<10 round-trips.
    """
    assert assemble(parse('**<10')) == '**<10'


def test_parse_le_not_lt_eq():
    """
    <= is parsed as a single operator, not < followed by =.
    """
    ops = parse('key<=7')
    assert len(ops) == 1
    assert assemble(ops) == 'key<=7'


def test_parse_ge_not_gt_eq():
    """
    >= is parsed as a single operator, not > followed by =.
    """
    ops = parse('key>=7')
    assert len(ops) == 1
    assert assemble(ops) == 'key>=7'


# ---------------------------------------------------------------------------
# Filter get: items[key<op>value]
#
# Filters produce a slice (list).  To access fields on each matched item,
# expand with [*] first: items[price>100][*].name — not items[price>100].name.
# ---------------------------------------------------------------------------

def test_filter_gt():
    """
    items[price>100] returns items where price > 100.
    """
    data = {'items': [{'price': 50}, {'price': 150}, {'price': 100}]}
    result = dotted.get(data, 'items[price>100]')
    assert result == [{'price': 150}]


def test_filter_ge():
    """
    items[price>=100] returns items where price >= 100.
    """
    data = {'items': [{'price': 50}, {'price': 150}, {'price': 100}]}
    result = dotted.get(data, 'items[price>=100]')
    assert result == [{'price': 150}, {'price': 100}]


def test_filter_lt():
    """
    items[price<100] returns items where price < 100.
    """
    data = {'items': [{'price': 50}, {'price': 150}, {'price': 100}]}
    result = dotted.get(data, 'items[price<100]')
    assert result == [{'price': 50}]


def test_filter_le():
    """
    items[price<=100] returns items where price <= 100.
    """
    data = {'items': [{'price': 50}, {'price': 150}, {'price': 100}]}
    result = dotted.get(data, 'items[price<=100]')
    assert result == [{'price': 50}, {'price': 100}]


def test_filter_with_continuation():
    """
    Filters produce a slice (list).  Use [*] to expand before accessing
    fields on each element: items[age>=18][*].name.
    """
    data = {'items': [
        {'name': 'alice', 'age': 25},
        {'name': 'bob', 'age': 12},
        {'name': 'carol', 'age': 18},
    ]}
    result = dotted.get(data, 'items[age>=18][*].name')
    assert result == ('alice', 'carol')


def test_filter_no_matches():
    """
    Filter with no matches returns empty.
    """
    data = {'items': [{'price': 50}, {'price': 60}]}
    result = dotted.get(data, 'items[price>100]')
    assert result == []


def test_filter_with_transform():
    """
    items[n|int<10] applies transform before comparison.
    """
    from dotted.results import Dotted
    old = Dotted._registry.get('int')
    dotted.register('int', lambda v: int(v))
    try:
        data = {'items': [{'n': '5'}, {'n': '15'}, {'n': '8'}]}
        result = dotted.get(data, 'items[n|int<10]')
        assert result == [{'n': '5'}, {'n': '8'}]
    finally:
        if old is not None:
            Dotted._registry['int'] = old
        else:
            Dotted._registry.pop('int', None)


# ---------------------------------------------------------------------------
# Value guard: key<op>value
# ---------------------------------------------------------------------------

def test_value_guard_gt():
    """
    *>10 matches values greater than 10.
    """
    data = {'a': 5, 'b': 15, 'c': 10}
    result = dotted.get(data, '*>10')
    assert result == (15,)


def test_value_guard_le():
    """
    *<=10 matches values less than or equal to 10.
    """
    data = {'a': 5, 'b': 15, 'c': 10}
    result = dotted.get(data, '*<=10')
    assert result == (5, 10)


def test_value_guard_lt():
    """
    *<10 matches values less than 10.
    """
    data = {'a': 5, 'b': 15, 'c': 10}
    result = dotted.get(data, '*<10')
    assert result == (5,)


def test_value_guard_ge():
    """
    *>=10 matches values greater than or equal to 10.
    """
    data = {'a': 5, 'b': 15, 'c': 10}
    result = dotted.get(data, '*>=10')
    assert result == (15, 10)


def test_slot_guard_gt():
    """
    [*]>5 on a list filters by value.
    """
    data = [3, 7, 5, 9]
    result = dotted.get(data, '[*]>5')
    assert result == (7, 9)


def test_slot_guard_le():
    """
    [*]<=5 on a list filters by value.
    """
    data = [3, 7, 5, 9]
    result = dotted.get(data, '[*]<=5')
    assert result == (3, 5)


# ---------------------------------------------------------------------------
# Recursive guard: **<op>value
# ---------------------------------------------------------------------------

def test_recursive_guard_lt():
    """
    **<10 matches all nested values less than 10.
    """
    data = {'a': 5, 'b': {'c': 15, 'd': 3}}
    result = dotted.get(data, '**<10')
    assert set(result) == {5, 3}


def test_recursive_guard_ge():
    """
    **>=10 matches all nested values >= 10.
    """
    data = {'a': 5, 'b': {'c': 15, 'd': 3}}
    result = dotted.get(data, '**>=10')
    assert result == (15,)


# ---------------------------------------------------------------------------
# Incomparable types: silently no-match
# ---------------------------------------------------------------------------

def test_incomparable_types_no_match():
    """
    Comparing incomparable types (str < int) silently produces no match.
    """
    data = {'items': [{'val': 'hello'}, {'val': 10}]}
    result = dotted.get(data, 'items[val>5]')
    assert result == [{'val': 10}]


# ---------------------------------------------------------------------------
# Backward compat: existing = and != still work
# ---------------------------------------------------------------------------

def test_filter_eq_unchanged():
    """
    items[name="alice"] still works.
    """
    data = {'items': [{'name': 'alice'}, {'name': 'bob'}]}
    result = dotted.get(data, 'items[name="alice"]')
    assert result == [{'name': 'alice'}]


def test_filter_neq_unchanged():
    """
    items[name!="alice"] still works.
    """
    data = {'items': [{'name': 'alice'}, {'name': 'bob'}]}
    result = dotted.get(data, 'items[name!="alice"]')
    assert result == [{'name': 'bob'}]


def test_value_guard_eq_unchanged():
    """
    key=value guard still works.
    """
    data = {'status': 'active', 'name': 'alice'}
    result = dotted.get(data, 'status="active"')
    assert result == 'active'


def test_value_guard_neq_unchanged():
    """
    key!=value guard still works.
    """
    data = {'a': 1, 'b': 2, 'c': 1}
    result = dotted.get(data, '*!=1')
    assert result == (2,)
